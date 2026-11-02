"""Regression contract for user-authorized Loom Embedded Browser control."""
from pathlib import Path

from app.agent_runtime.browser_backend_registry import EMBEDDED_BROWSER, normalize_browser_backend
from app.settings import LoomSettingsStore

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "desktop-react/electron/embeddedBrowser.ts"
BRIDGE = ROOT / "desktop-react/electron/embeddedBrowserAgentBridge.ts"
MAIN = ROOT / "desktop-react/electron/main.ts"


def test_embedded_backend_is_explicit_and_persistable():
    assert normalize_browser_backend("embedded") == EMBEDDED_BROWSER
    assert normalize_browser_backend("embedded-browser") == EMBEDDED_BROWSER
    assert LoomSettingsStore._validate_setting("browser.mode", "embedded") == "embedded"


def test_native_control_requires_human_tab_grant_and_no_debug_port():
    source = MANAGER.read_text(encoding="utf-8")
    assert 'case "allow-ai"' in source
    assert "this.aiAllowed.has(tabId)" in source
    assert "this.aiLeases.get(tabId)" in source
    assert "this.aiAllowed.delete(command.tabId)" in source
    assert "nodeIntegration: false" in source
    assert "sandbox: true" in source
    assert "remote-debugging-port" not in source
    main = MAIN.read_text(encoding="utf-8")
    assert "embeddedBrowserToken()" in main


def test_tabs_a_task_opens_are_its_own_and_never_the_users():
    # Behaviour is exercised in real Electron by scripts/tests/embedded-browser-agent-electron.mjs.
    source = MANAGER.read_text(encoding="utf-8")
    assert 'const AGENT_PARTITION = "loom-embedded-agent";' in source  # in memory: no "persist:" prefix
    assert 'this.addTab("", true, undefined, false, AGENT_PARTITION)' in source
    assert "!this.tabs.get(id)?.state.agentOwned" in source  # never restored
    assert "if (!entry.state.agentOwned) {" in source  # a user's tab is not pulled in front
    main = MAIN.read_text(encoding="utf-8")
    assert "startEmbeddedBrowserAgentClient(window);" in main


def test_bridge_is_isolated_and_authenticated():
    source = BRIDGE.read_text(encoding="utf-8")
    assert 'http://127.0.0.1:39224' in source  # 39223 is Host discovery
    assert '"X-Loom-Token": this.token' in source
    assert "this.manager().agentCommand(action, args)" in source
    assert "this.abort.abort()" in source
    assert "No CDP debug port is enabled" in source


def test_the_decorative_hud_cannot_hold_an_action():
    # A task's new tab has never loaded a page, and a script there waits for a
    # document: the first navigate after a start hung behind the HUD for 45 s.
    source = (ROOT / "desktop-react/electron/embeddedBrowserHud.ts").read_text(encoding="utf-8")
    assert source.count("executeJavaScriptInIsolatedWorld") == 1, "every HUD script goes through runHudScript"
    assert "Promise.race([wc.executeJavaScriptInIsolatedWorld" in source
    assert "HUD_SCRIPT_LIMIT_MS = 1500" in source


def test_a_scroll_that_reaches_the_end_waits_for_an_endless_list():
    source = MANAGER.read_text(encoding="utf-8")
    assert "root.scrollHeight > height" in source
    assert "innerHeight + scrollY >= root.scrollHeight - 60" in source


def test_loom_browser_runs_the_extension_page_code():
    # Its own 250-node "N: <tag> text" list had no labels or states and browser_batch
    # could not read it; one page code now serves every route.
    import re
    import shutil
    import subprocess
    import tempfile

    loader = (ROOT / "desktop-react/electron/pageActions.ts").read_text(encoding="utf-8")
    assert r'"\nfunction runPageAction("' in loader and r'replace(/\r\n/g, "\n")' in loader
    background = (ROOT / "extensions/browser-current-tab/background.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = background.index("\nfunction runPageAction(")
    body = background[start + 1:background.index("\n}\n", start + 1) + 2]
    assert body.startswith("function runPageAction(") and body.rstrip().endswith("}")
    if shutil.which("node"):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write("(" + body + ");\n")
        assert subprocess.run(["node", "--check", handle.name], capture_output=True).returncode == 0

    source = MANAGER.read_text(encoding="utf-8")
    for action in ('"state"', '"prepare_click"', '"prepare_type"', '"select_option"', '"frame_offset"', '"click_at"'):
        assert action in source
    assert "aiSignatures" not in source
    assert "page_hud: false" in source
    main = MAIN.read_text(encoding="utf-8")
    assert 'loadRunPageAction(path.join(browserExtensionSource(), "background.js"))' in main
    assert "args.page_hud === false" in background


def test_loom_browser_input_reaches_tabs_that_are_not_on_screen():
    # Electron delivers mouse and key input only to an attached view: clicks on a
    # task's tab that the panel was not showing were dropped (live, 2026-11-02).
    source = MANAGER.read_text(encoding="utf-8")
    on_screen = source[source.index("private async agentOnScreen"):]
    assert "this.attached === entry.view" in on_screen
    assert "(!active || active.state.agentOwned)" in on_screen  # never over the user's own tab
    element = source[source.index("private async agentElement"):]
    assert "live && ref.ordinal < 0" in element  # frames and off-screen tabs use page code
    assert "insertTextScript(value)" in element
    # A tab never shown lays out at 0x0 with nothing visible.
    assert "view.setBounds({ x: 0, y: 0, width: Math.max(800" in source
    bridge = BRIDGE.read_text(encoding="utf-8")
    assert 'const VERSION = "0.1.27";' in bridge

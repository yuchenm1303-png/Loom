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
    assert 'http://127.0.0.1:39223' in source
    assert '"X-Loom-Token": this.token' in source
    assert "this.manager().agentCommand(action, args)" in source
    assert "this.abort.abort()" in source
    assert "No CDP debug port is enabled" in source

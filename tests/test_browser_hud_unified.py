from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "browser-current-tab"


def test_extension_loads_standalone_browser_hud_inside_web_pages():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest.get("content_scripts") or []
    assert any(
        (entry.get("js") or []) == ["browser-hud.js"]
        and set(entry.get("matches") or ()) >= {"http://*/*", "https://*/*"}
        for entry in scripts
    )


def test_browser_hud_keeps_dom_target_and_owns_its_visual_lifecycle():
    source = (EXTENSION / "browser-hud.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert "loom-page-hud-target" in background
    assert "loom-page-hud-label" in background
    assert "showTargetHud" in background

    assert "loom-browser-hud-root-v2" in source
    assert "__loomBrowserHudRuntimeV2" in source
    assert "cloneNode(true)" not in source
    assert "IDLE_HIDE_MS" not in source

    assert "class=\"edge\"" in source
    assert 'id="cursor" class="cursor"' in source
    assert "class=\"bubble\"" in source
    assert "id=\"timeline\"" in source
    assert "getBoundingClientRect()" in source
    assert "DOM exact" in source
    assert "browser + DOM" in source
    assert "position:fixed;inset:0" in source

    assert "userTakesOver" not in source
    assert "event.isTrusted" not in source
    # present() is what a browser with no extension in it calls: the asset is
    # injected over CDP there, so the exported surface is the whole interface.
    assert "globalThis[INSTALL_KEY] = { generation: GENERATION, sync, hide, dispose, present }" in source
    assert "existing?.dispose?.()" in source


def test_browser_hud_does_not_reenable_desktop_overlay_for_browser_tools():
    policy = (ROOT / "app" / "app_server_browser_policy.py").read_text(encoding="utf-8")
    assert 'return "computer" if name.startswith("computer_") else ""' in policy
    assert 'if str(tool_name or "").startswith("browser_"):' in policy
    assert "return None" in policy
    # ...which is only defensible while the page-local HUD reaches every browser,
    # not only the one with the extension in it. It did not, and a browser Loom
    # launched itself then ran with no HUD of either kind.
    assert '"page-local"' in policy


def test_the_page_hud_reaches_browsers_that_have_no_extension():
    backend = (ROOT / "app" / "agent_runtime" / "browser_use_backend.py").read_text(encoding="utf-8")
    driver = (ROOT / "app" / "agent_runtime" / "browser_page_hud.py").read_text(encoding="utf-8")

    assert "self._announce_action(action, args)" in backend
    assert "browser-hud.js" in driver
    assert "addScriptToEvaluateOnNewDocument" in driver

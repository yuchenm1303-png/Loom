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

    # The existing page-local DOM HUD remains the exact target source.
    assert "loom-page-hud-target" in background
    assert "loom-page-hud-label" in background
    assert "showTargetHud" in background

    # Browser Use owns a standalone persistent renderer. It does not clone the
    # Electron Computer HUD or mirror another browser-side Computer HUD layer.
    assert "loom-browser-hud-root" in source
    assert "__loomBrowserHudStandaloneV1" in source
    assert "cloneNode(true)" not in source
    assert "IDLE_HIDE_MS = 60000" in source

    # It still uses the same visual language and real DOM geometry.
    assert "class=\"edge\"" in source
    assert "id=\"cursor\"" in source
    assert "class=\"bubble\"" in source
    assert "id=\"timeline\"" in source
    assert "getBoundingClientRect()" in source
    assert "DOM exact" in source
    assert "browser + DOM" in source
    assert "position:fixed;inset:0" in source


def test_browser_hud_does_not_reenable_desktop_overlay_for_browser_tools():
    policy = (ROOT / "app" / "app_server_browser_policy.py").read_text(encoding="utf-8")
    assert 'return "computer" if name.startswith("computer_") else ""' in policy
    assert 'if str(tool_name or "").startswith("browser_"):' in policy
    assert "return None" in policy

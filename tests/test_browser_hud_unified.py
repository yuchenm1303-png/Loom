from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "browser-current-tab"


def test_extension_loads_computer_hud_overlay_inside_web_pages():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest.get("content_scripts") or []
    assert any(
        "hud-computer-overlay.js" in (entry.get("js") or [])
        and set(entry.get("matches") or ()) >= {"http://*/*", "https://*/*"}
        for entry in scripts
    )


def test_browser_hud_keeps_dom_target_and_layers_computer_use_visuals():
    source = (EXTENSION / "hud-computer-overlay.js").read_text(encoding="utf-8")
    # The existing page-local DOM HUD remains the source of truth for the target.
    assert ".loom-page-hud-target" in source
    assert ".loom-page-hud-label" in source
    # Computer Use's visual language is layered on top, scoped to the browser viewport.
    assert "loom-computer-hud-layer" in source
    assert "loom-cu-edge" in source
    assert "loom-cu-cursor" in source
    assert "loom-cu-bubble" in source
    assert "loom-cu-timeline" in source
    assert "DOM exact" in source
    assert "browser + DOM" in source
    assert "position:fixed;inset:0" in source


def test_browser_hud_does_not_reenable_desktop_overlay_for_browser_tools():
    policy = (ROOT / "app" / "app_server_browser_policy.py").read_text(encoding="utf-8")
    assert 'return "computer" if name.startswith("computer_") else ""' in policy
    assert 'if str(tool_name or "").startswith("browser_"):' in policy
    assert "return None" in policy

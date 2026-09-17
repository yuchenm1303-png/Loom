from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "browser-current-tab"


def test_browser_hud_keeps_dom_target_and_computer_visual_layer() -> None:
    background = (EXT / "background.js").read_text(encoding="utf-8")
    overlay = (EXT / "hud-computer-overlay.js").read_text(encoding="utf-8")
    host = (EXT / "hud-computer-host.js").read_text(encoding="utf-8")

    # The original exact DOM target HUD remains the source of truth.
    assert "loom-browser-page-hud-root" in background
    assert "loom-page-hud-target" in background
    assert "showTargetHud" in background

    # The Computer visual layer remains present, but a second host owns its paint
    # lifecycle so background.js cannot erase it with root.innerHTML rebuilds.
    assert "loom-computer-hud-layer" in overlay
    assert "loom-browser-computer-hud-root" in host
    assert "const CLONE_ID = SOURCE_LAYER_ID" in host
    assert "cloneNode(true)" in host
    assert "layer.style.display = 'none'" in host
    assert "sourceObserver.observe(sourceRoot" in host

    # Real browser target geometry still drives the browser-local cursor/bubble.
    assert "getBoundingClientRect()" in overlay
    assert "DOM exact" in overlay
    assert "browser + DOM" in overlay

    for token in ("loom-cu-edge", "loom-cu-pill", "loom-cu-cursor", "loom-cu-bubble", "loom-cu-timeline"):
        assert token in overlay


def test_hud_is_injected_into_existing_tabs_after_extension_reload() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    worker = (EXT / "bridge-worker.js").read_text(encoding="utf-8")

    assert manifest["background"]["service_worker"] == "bridge-worker.js"
    assert manifest["version"] == "0.1.2"
    scripts = manifest.get("content_scripts", [])[0]["js"]
    assert scripts == ["hud-computer-overlay.js", "hud-computer-host.js"]

    # Reloading an unpacked extension must repair already-open tabs too. Users
    # should not have to refresh Cloudflare just to see the HUD layer appear.
    assert "import './background.js'" in worker
    assert "chrome.tabs.query({})" in worker
    assert "chrome.scripting.executeScript" in worker
    assert "files: HUD_SCRIPTS" in worker
    assert "__loomBrowserHudHostV2" in worker


def test_browser_computer_hud_tracks_desktop_visual_contract() -> None:
    desktop = (ROOT / "desktop-react" / "electron" / "hudWindow.ts").read_text(encoding="utf-8")
    host = (EXT / "hud-computer-host.js").read_text(encoding="utf-8")
    overlay = (EXT / "hud-computer-overlay.js").read_text(encoding="utf-8")

    # High-salience dimensions are locked against the desktop HUD so Browser Use
    # cannot quietly drift back into a different design language.
    assert "--bubble-width:420px" in desktop
    assert "--bubble-width:420px" in host
    assert "top:20px" in desktop
    assert "top:20px" in host
    assert "bottom:24px" in desktop
    assert "bottom:24px" in host
    assert "width:82px;height:82px" in desktop
    assert "width:82px;height:82px" in overlay
    assert "width:36px;height:36px" in desktop
    assert "width:36px;height:36px" in overlay
    assert "filter:blur(14px) saturate(1.16);opacity:.42" in desktop
    assert "opacity:.42!important" in host
    assert "filter:blur(8px) saturate(1.3);opacity:.68" in desktop
    assert "opacity:.68!important" in host

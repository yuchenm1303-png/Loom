from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "browser-current-tab"


def test_browser_hud_is_standalone_and_keeps_dom_target_overlay() -> None:
    background = (EXT / "background.js").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    # The exact DOM target frame remains owned by the browser action layer.
    assert "loom-browser-page-hud-root" in background
    assert "loom-page-hud-target" in background
    assert "showTargetHud" in background

    # Browser Use owns its own renderer and lifecycle. It no longer clones the
    # Computer Use renderer or depends on the old two-script mirror.
    assert "loom-browser-hud-root" in browser_hud
    assert "__loomBrowserHudStandaloneV1" in browser_hud
    assert "cloneNode(true)" not in browser_hud
    assert "loom-browser-computer-hud-root" in browser_hud  # legacy cleanup only
    assert "IDLE_HIDE_MS = 60000" in browser_hud
    assert "sourceObserver.observe" in browser_hud

    # Real DOM geometry drives a persistent virtual cursor and action metadata.
    assert "getBoundingClientRect()" in browser_hud
    assert "DOM exact" in browser_hud
    assert "browser + DOM" in browser_hud
    assert "cursor hidden" in browser_hud
    assert "clicking" in browser_hud

    for token in ("class=\"edge\"", "class=\"pill\"", "id=\"cursor\"", "class=\"bubble\"", "id=\"timeline\""):
        assert token in browser_hud


def test_browser_hud_is_injected_into_existing_tabs_after_extension_reload() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    worker = (EXT / "bridge-worker.js").read_text(encoding="utf-8")

    assert manifest["background"]["service_worker"] == "bridge-worker.js"
    assert manifest["version"] == "0.1.6"
    scripts = manifest.get("content_scripts", [])[0]["js"]
    assert scripts == ["browser-hud.js"]

    # Reloading an unpacked extension repairs already-open tabs too.
    assert "import './background.js'" in worker
    assert "chrome.tabs.query({})" in worker
    assert "chrome.scripting.executeScript" in worker
    assert "files: HUD_SCRIPTS" in worker
    assert "__loomBrowserHudStandaloneV1" in worker
    assert "hud-computer-overlay.js" not in worker
    assert "hud-computer-host.js" not in worker


def test_browser_hud_tracks_computer_visual_language_without_sharing_lifecycle() -> None:
    desktop = (ROOT / "desktop-react" / "electron" / "hudWindow.ts").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    # High-salience dimensions stay aligned, but the renderers are separate.
    for token in (
        "--bubble-width:420px",
        "top:20px",
        "bottom:24px",
        "width:82px;height:82px",
        "width:36px;height:36px",
        "filter:blur(14px) saturate(1.16);opacity:.42",
        "filter:blur(8px) saturate(1.3);opacity:.68",
    ):
        assert token in desktop
        assert token in browser_hud

    assert "BrowserWindow" in desktop
    assert "loom-browser-hud-root" in browser_hud
    assert "cloneNode(true)" not in browser_hud


def test_browser_hud_stays_alive_between_actions_and_hides_on_real_user_takeover() -> None:
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    # The renderer host persists; only visibility changes after a long idle window.
    assert "renderer.hud.classList.remove('live')" in browser_hud
    assert "host.remove()" not in browser_hud
    assert "setTimeout(hide, IDLE_HIDE_MS)" in browser_hud

    # A real human taking back the page dismisses the overlay without relying on
    # browser actions tearing the renderer down.
    assert "event.isTrusted" in browser_hud
    assert "pointerdown" in browser_hud
    assert "keydown" in browser_hud
    assert "wheel" in browser_hud

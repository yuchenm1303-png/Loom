from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "browser-current-tab"


def test_browser_hud_is_standalone_and_keeps_dom_target_overlay() -> None:
    background = (EXT / "background.js").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    assert "loom-browser-page-hud-root" in background
    assert "loom-page-hud-target" in background
    assert "showTargetHud" in background

    assert "loom-browser-hud-root-v2" in browser_hud
    assert "__loomBrowserHudRuntimeV2" in browser_hud
    assert "GENERATION = '0.1.9'" in browser_hud
    assert "cloneNode(true)" not in browser_hud
    assert "sourceObserver.observe" in browser_hud

    assert "getBoundingClientRect()" in browser_hud
    assert "DOM exact" in browser_hud
    assert "browser + DOM" in browser_hud
    assert 'id="cursor" class="cursor"' in browser_hud
    assert 'class="cursor hidden"' not in browser_hud
    assert "opacity:1!important;visibility:visible!important" in browser_hud
    assert "z-index:80" in browser_hud
    assert "clicking" in browser_hud

    for token in ("class=\"edge\"", "class=\"pill\"", "id=\"cursor\"", "class=\"bubble\"", "id=\"timeline\""):
        assert token in browser_hud


def test_browser_hud_hot_reload_replaces_old_runtime_in_existing_tabs() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    worker = (EXT / "bridge-worker.js").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    assert manifest["background"]["service_worker"] == "bridge-worker.js"
    assert tuple(int(part) for part in manifest["version"].split(".")) >= (0, 1, 9)
    assert manifest.get("content_scripts", [])[0]["js"] == ["browser-hud.js"]

    assert "import './background.js'" in worker
    assert "chrome.tabs.query({})" in worker
    assert "chrome.scripting.executeScript" in worker
    assert "files: HUD_SCRIPTS" in worker
    assert "__loomBrowserHudRuntimeV2" in worker
    assert "__loomBrowserHudStandaloneV1" not in worker

    assert "existing?.dispose?.()" in browser_hud
    assert "function dispose()" in browser_hud
    assert "LEGACY_SUPPRESSOR_ID" in browser_hud
    assert "loom-browser-hud-root-v2" in browser_hud
    assert "loom-browser-hud-root'" in browser_hud  # old host is suppressed, never reused


def test_browser_hud_tracks_computer_visual_language_without_sharing_lifecycle() -> None:
    desktop = (ROOT / "desktop-react" / "electron" / "hudWindow.ts").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    for token in (
        "--bubble-width:420px",
        "top:20px",
        "bottom:24px",
        "width:82px;height:82px",
        "filter:blur(14px) saturate(1.16);opacity:.42",
        "filter:blur(8px) saturate(1.3);opacity:.68",
    ):
        assert token in desktop
        assert token in browser_hud

    assert "BrowserWindow" in desktop
    assert "loom-browser-hud-root-v2" in browser_hud
    assert "cloneNode(true)" not in browser_hud


def test_browser_hud_all_visual_layers_stay_visible_during_user_interaction() -> None:
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    assert "view.hud.classList.add('live')" in browser_hud
    assert "IDLE_HIDE_MS" not in browser_hud
    assert "setTimeout(hide" not in browser_hud

    assert "event.isTrusted" not in browser_hud
    assert "userTakesOver" not in browser_hud
    assert "addEventListener('pointerdown'" not in browser_hud
    assert "addEventListener('keydown'" not in browser_hud
    assert "addEventListener('wheel'" not in browser_hud

    assert "lastX = width * 0.5" in browser_hud
    assert "lastY = height * 0.46" in browser_hud
    assert 'id="cursor" class="cursor"' in browser_hud
    assert "cursor.classList.remove('hidden')" not in browser_hud

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "browser-current-tab"


def test_home_preview_uses_the_native_hud_document_without_custom_effects() -> None:
    native = (ROOT / "desktop-react/electron/hudWindow.ts").read_text(encoding="utf-8")
    preview = (ROOT / "desktop-react/src/components/HomeComputerHud.tsx").read_text(encoding="utf-8")
    css = (ROOT / "desktop-react/src/components/home-computer-demo.css").read_text(encoding="utf-8")
    assert 'import { hudDocument } from "./hudDocument.js"' in native
    assert 'import { hudDocument } from "../../electron/hudDocument"' in preview
    assert 'hudDocument().replace("<script>"' in preview
    assert 'sandbox="allow-scripts"' in preview
    for removed in ("cu-demo-", "computer-demo-scan", "computer-demo-progress", "computer-demo-cursor", "computer-demo-edge"):
        assert removed not in css


def _edge_rules(source: str) -> list[str]:
    """The stylesheet rules of the edge glow, one per line, whatever the indentation."""
    return [line.strip() for line in source.splitlines() if line.strip().startswith(".edge")]


def test_browser_hud_is_standalone_and_keeps_dom_target_overlay() -> None:
    background = (EXT / "background.js").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    assert "loom-browser-page-hud-root" in background
    assert "loom-page-hud-target" in background
    assert "showTargetHud" in background

    assert "loom-browser-hud-root-v2" in browser_hud
    assert "__loomBrowserHudRuntimeV2" in browser_hud
    # Not pinned to a literal: GENERATION exists so an already-injected older
    # runtime disposes itself and hands over, which means it is supposed to change
    # whenever this file does. Pinning it made every HUD change fail this test for
    # the one reason that is not a regression.
    assert re.search(r"GENERATION = '\d+\.\d+\.\d+'", browser_hud)
    assert "existing?.generation === GENERATION" in browser_hud
    assert "existing?.dispose?.()" in browser_hud
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

    for token in ("class=\"edge\"", "class=\"pill\"", "id=\"cursor\"", "class=\"bubble\""):
        assert token in browser_hud


def test_phase_bar_is_removed_from_all_hud_renderers() -> None:
    sources = [
        EXT / "browser-hud.js",
        ROOT / "desktop-react" / "electron" / "hudDocument.ts",
        ROOT / "desktop-react" / "src" / "components" / "ComputerUseHudOverlay.tsx",
        ROOT / "desktop-react" / "src" / "components" / "computer-use-hud.css",
    ]
    for path in sources:
        source = path.read_text(encoding="utf-8")
        for removed in ('id="timeline"', "renderTimeline", "hud-bottom-timeline", "hud-phase", ".timeline{"):
            assert removed not in source, (path, removed)


def test_browser_hud_hot_reload_replaces_old_runtime_in_existing_tabs() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    worker = (EXT / "bridge-worker.js").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    assert manifest["background"]["service_worker"] == "bridge-worker.js"
    # 0.1.6 is where the standalone HUD landed, and this floor stays there. Reload
    # repair depends on the worker and the content scripts below, never on the
    # version string, so raising this with each extension bump asserts nothing and
    # only turns unrelated extension work into a failure of a HUD test - which it
    # already did twice, at 0.1.7 and 0.1.9.
    assert tuple(int(part) for part in manifest["version"].split(".")) >= (0, 1, 6)
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
    desktop = (ROOT / "desktop-react" / "electron" / "hudDocument.ts").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    for token in ("--bubble-width:420px", "top:20px", "width:82px;height:82px"):
        assert token in desktop
        assert token in browser_hud

    # The edge glow layers intentionally share the same visual parameters (colours, fade, breathing
    # range); their runtime/lifecycle implementations remain independent.
    desktop_edge = _edge_rules(desktop)
    assert desktop_edge == _edge_rules(browser_hud)
    assert len(desktop_edge) >= 8
    assert "BrowserWindow" in (ROOT / "desktop-react/electron/hudWindow.ts").read_text(encoding="utf-8")
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


def test_automation_huds_do_not_run_a_full_viewport_effect_stack() -> None:
    """The automation overlay is on screen for the whole of a browser or desktop task.

    Two full-viewport conic-gradient layers with a blur filter, backdrop filters on
    three panels and infinite animations over all of it kept the integrated GPU of
    the development machine at 99-100% (measured: any other window dropped from
    165 to 62-101 fps while the overlay was up). The edge glow is now four thin
    strips per layer with its colours and falloff baked in, breathing on a 12 Hz
    timer; none of the following may come back.
    """

    desktop = (ROOT / "desktop-react" / "electron" / "hudDocument.ts").read_text(encoding="utf-8")
    browser_hud = (EXT / "browser-hud.js").read_text(encoding="utf-8")

    for name, source in (("hudWindow.ts", desktop), ("browser-hud.js", browser_hud)):
        assert "backdrop-filter" not in source, name
        assert "filter:blur(14px)" not in source and "filter:blur(8px)" not in source, name
        assert "::before{--breath" not in source and "edge::after" not in source, name
        # No CSS animation runs on the glow itself: it is driven by a timer.
        for rule in _edge_rules(source):
            assert "animation" not in rule, (name, rule[:60])
        assert source.count('<i class="a n"></i>') == 1 and source.count('<i class="b e"></i>') == 1, name
        assert "setInterval(breathe" in source, name
        # No ambient loop may animate a paint-only property (box-shadow) on the main thread.
        assert "@keyframes pulse{" not in source and "@keyframes loomBrowserPulse" not in source, name
        assert "@keyframes orbit" not in source and "@keyframes loomBrowserOrbit" not in source, name

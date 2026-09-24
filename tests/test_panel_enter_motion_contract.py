from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_sidebar_and_inspector_expose_linked_motion_phases() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "useLinkedPanelMotion" in app
    assert 'type LinkedPanelPhase = "closed" | "opening" | "open" | "closing"' in app
    assert "PANEL_LAYOUT_SETTLE_MS = 390" in app
    assert 'linkedPanelPhaseClass("sidebar", sidebarMotion.phase)' in app
    assert 'linkedPanelPhaseClass("inspector", inspectorMotion.phase)' in app
    assert ".sidebar-motion-opening" in css
    assert ".sidebar-motion-closing" in css
    assert ".inspector-motion-opening" in css
    assert ".inspector-motion-closing" in css


def test_main_workspace_moves_with_panel_on_the_compositor() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "transition: grid-template-columns" not in css
    assert ".workspace-panels.is-layout-coupled .thread-header-leading" in css
    assert ".workspace-panels.is-layout-coupled .polished-thread-header-actions" in css
    assert ".workspace-panels.is-layout-coupled .transcript" in css
    assert ".workspace-panels.is-layout-coupled .composer" in css
    assert "@keyframes loom-linked-left-edge" in css
    assert "@keyframes loom-linked-right-edge" in css
    assert "@keyframes loom-linked-center" in css
    assert "will-change: translate" in css

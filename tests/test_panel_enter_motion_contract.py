from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_open_uses_overlay_first_layout_reservation() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "usePanelLayoutReserve" in app
    assert "PANEL_LAYOUT_SETTLE_MS" in app
    assert "sidebarVisualOpen" not in app
    assert "sidebarEnterFrameRef" not in app
    assert 'sidebarOpen ? "sidebar-open" : "sidebar-closed"' in app
    assert 'inspectorVisible ? "inspector-open" : "inspector-closed"' in app
    assert ".sidebar-layout-closed > .sidebar" in css
    assert ".inspector-layout-closed > .inspector" in css
    assert "position: absolute" in css
    assert "grid-column: 2" in css


def test_panel_motion_is_compositor_first_without_blank_track() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "transition: grid-template-columns" not in css
    assert "translate3d(calc(-100% - 1px),0,0)" in css
    assert "translate3d(calc(100% + 1px),0,0)" in css
    assert "opacity var(--loom-motion-opacity" not in css
    assert ".workspace-panels.is-panel-motion > .inspector .runtime-pane" in css
    assert "animation: none !important;" in css


def test_right_side_docks_reserve_space_only_after_their_overlay_enters() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "reviewLayoutOpen = usePanelLayoutReserve(reviewOpen)" in app
    assert "agentsLayoutOpen = usePanelLayoutReserve(agentsOpen)" in app
    assert "projectDetailsLayoutOpen = usePanelLayoutReserve(projectDetailsOpen)" in app
    assert '${reviewLayoutOpen ? "with-review" : ""}' in app
    assert '${agentsLayoutOpen ? "with-agents" : ""}' in app
    assert '${projectDetailsLayoutOpen ? "with-project-details" : ""}' in app

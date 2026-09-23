from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_open_stages_layout_then_visual_motion() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "sidebarVisualOpen" in app
    assert "inspectorVisualOpen" in app
    assert "sidebarEnterFrameRef" in app
    assert "inspectorEnterFrameRef" in app
    assert app.count("requestAnimationFrame(() => {") >= 4
    assert 'sidebarVisualOpen ? "sidebar-open" : "sidebar-closed"' in app
    assert 'inspectorVisualOpen ? "inspector-open" : "inspector-closed"' in app


def test_panel_motion_remains_compositor_only() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "transition: grid-template-columns" not in css
    assert "translate3d(-18px,0,0)" in css
    assert "translate3d(18px,0,0)" in css
    assert ".workspace-panels.is-panel-motion > .inspector .runtime-pane" in css
    assert "animation: none !important;" in css

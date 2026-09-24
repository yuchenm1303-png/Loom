from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_motion_uses_live_targeted_flip_not_view_transition_snapshots() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "captureLayoutAnchors" in app
    assert "animateLayoutAnchors" in app
    assert 'selector: ".conversation-stage"' in app
    assert 'selector: ".composer-stage"' in app
    assert 'animation.id = "loom-layout-anchor"' in app

    assert "startViewTransition" not in app
    assert "LayoutViewTransition" not in app
    assert "loomLayoutCapture" not in app
    assert "::view-transition" not in css
    assert "view-transition-name" not in css


def test_sidebar_and_inspector_move_locally_without_animating_all_children() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert ".workspace-panels.sidebar-layout-closed > .sidebar" in css
    assert ".workspace-panels.inspector-layout-closed > .inspector" in css
    assert "translate3d(-18px,0,0)" in css
    assert "translate3d(18px,0,0)" in css
    assert "visibility 0s linear 240ms" in css

    assert ".thread-header-copy" not in css
    assert "loom-readable" not in css
    assert "loom-header-copy-old" not in css
    assert "loom-transcript-old" not in css


def test_only_large_content_stages_receive_layout_motion_hint() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "body.loom-panel-motion .workspace-panels .conversation-stage" in css
    assert "body.loom-panel-motion .workspace-panels .composer-stage" in css
    assert "body.loom-panel-motion .workspace-panels .thread-header" not in css

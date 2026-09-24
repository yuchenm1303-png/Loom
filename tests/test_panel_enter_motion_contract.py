from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_motion_uses_targeted_flip_plus_scoped_panel_snapshots() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "captureLayoutAnchors" in app
    assert "animateLayoutAnchors" in app
    assert 'selector: ".conversation-stage"' in app
    assert 'selector: ".composer-stage"' in app
    assert 'animation.id = "loom-layout-anchor"' in app

    assert "startViewTransition" in app
    assert 'dataset.loomPanelSnapshot = "true"' in app
    assert 'html[data-loom-panel-snapshot="true"] {' in css
    assert "view-transition-name: loom-sidebar" in css
    assert "view-transition-name: loom-inspector" in css
    assert "view-transition-name: loom-workspace" not in css
    assert "loom-readable" not in css

def test_sidebar_and_inspector_move_locally_without_animating_all_children() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert ".workspace-panels.sidebar-layout-closed > .sidebar" in css
    assert ".workspace-panels.inspector-layout-closed > .inspector" in css
    assert "translate3d(-12px,0,0)" in css
    assert "translate3d(12px,0,0)" in css
    assert "visibility 0s linear 230ms" in css
    assert "animation: loom-panel-surface-in-left 292ms" in css
    assert "animation: loom-panel-surface-in-right 292ms" in css

    assert ".thread-header-copy" not in css
    assert "loom-readable" not in css
    assert "loom-header-copy-old" not in css
    assert "loom-transcript-old" not in css


def test_only_large_content_stages_receive_layout_motion_hint() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "body.loom-panel-motion .workspace-panels .conversation-stage" in css
    assert "body.loom-panel-motion .workspace-panels .composer-stage" in css
    assert "body.loom-panel-motion .workspace-panels .thread-header" not in css


def test_content_flip_is_horizontal_only_and_interruptible() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "centerY" not in app
    assert "animation.commitStyles()" in app
    assert "settleLayoutAnchorAnimations" in app
    assert "clearLayoutAnchorStyles" in app
    assert 'transform: `translate3d(${deltaX}px,0,0)`' in app
    assert 'duration: capture.kind === "conversation" ? 270 : 285' in app


def test_panel_css_has_one_owner_for_open_close_motion() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert css.count(".workspace-panels.sidebar-open > .sidebar") == 1
    assert css.count(".workspace-panels.sidebar-closed > .sidebar") == 1
    assert css.count(".workspace-panels.inspector-open > .inspector") == 1
    assert css.count(".workspace-panels.inspector-closed > .inspector") == 1


def test_panel_surface_uses_corner_origin_flight_without_global_snapshot_animation() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "transform-origin: left top" in css
    assert "transform-origin: right top" in css
    assert "animation: loom-panel-surface-in-left 360ms cubic-bezier(.16,1,.3,1) both" in css
    assert "animation: loom-panel-surface-in-right 360ms cubic-bezier(.16,1,.3,1) both" in css
    assert "translate3d(-20px,-12px,0) scale(.976)" in css
    assert "translate3d(20px,-12px,0) scale(.976)" in css
    assert "scale(1.002)" in css
    assert "view-transition-name: loom-workspace" not in css
    assert "loom-readable" not in css


def test_inspector_micro_motion_is_frozen_during_panel_snapshot() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert ".runtime-pane" in css
    assert ".runtime-orbit-dot" in css
    assert ".runtime-orbit-one::before" in css
    assert ".runtime-orbit-two::before" in css
    assert "animation-play-state: paused !important" in css


def test_corner_flight_keeps_inspector_content_frozen_during_handoff() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "body.loom-panel-motion .workspace-panels > .inspector .runtime-pane" in css
    assert "animation: none !important" in css
    assert "animation-play-state: paused !important" in css

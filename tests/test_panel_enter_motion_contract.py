from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_side_panel_layout_uses_one_view_transition_coordinator() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert 'import { flushSync } from "react-dom"' in app
    assert "startViewTransition" in app
    assert "runLayoutTransition" in app
    assert "layoutViewTransitionRef.current?.skipTransition()" in app
    assert 'document.documentElement.dataset.loomLayoutTransition = "true"' in app
    assert 'document.documentElement.dataset.loomLayoutCapture = "old"' in app
    assert 'document.documentElement.dataset.loomLayoutCapture = "new"' in app

    assert "usePanelLayoutReserve" not in app
    assert "useLinkedPanelMotion" not in app
    assert "linkedWorkspaceMotion" not in app
    assert "PANEL_LAYOUT_SETTLE_MS" not in app

    assert "view-transition-name: loom-workspace" in css
    assert "view-transition-name: loom-sidebar" in css
    assert "view-transition-name: loom-inspector" in css
    assert "::view-transition-group(loom-workspace)" in css


def test_workspace_geometry_is_not_animated_by_fragment_transforms() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "transition: grid-template-columns" not in css
    assert "is-layout-coupled" not in css
    assert "loom-linked-center" not in css
    assert "sidebar-motion-opening" not in css
    assert ".workspace-panels.is-layout-coupled .transcript" not in css
    assert "body.loom-panel-motion .workspace-panels .transcript-scroll" in css


def test_readable_layers_crossfade_at_native_geometry() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert 'html[data-loom-layout-capture="old"] .transcript' in css
    assert 'html[data-loom-layout-capture="new"] .transcript' in css
    assert "loom-transcript-old" in css
    assert "loom-transcript-new" in css
    assert "loom-composer-old" in css
    assert "loom-composer-new" in css
    assert "loom-header-copy-old" in css
    assert "loom-header-copy-new" in css
    assert "@keyframes loom-readable-out" in css
    assert "@keyframes loom-readable-in" in css
    assert "::view-transition-group(loom-transcript-old)" in css


def test_portal_surfaces_capture_their_logical_endpoint() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")
    review = read("desktop-react/src/components/ReviewWorkspace.tsx")
    agents = read("desktop-react/src/components/SubAgentDock.tsx")
    project = read("desktop-react/src/components/ProjectDetailsPanel.tsx")

    assert 'data-open={open ? "true" : "false"}' in review
    assert 'data-open={open ? "true" : "false"}' in agents
    assert 'data-open={open ? "true" : "false"}' in project
    assert '.review-workspace[data-open="true"]' in css
    assert '.review-workspace[data-open="false"]' in css


def test_readable_motion_has_direction_scale_and_stagger() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "type LayoutMotionIntent" in app
    assert 'document.documentElement.dataset.loomLayoutIntent = intent' in app
    assert 'sidebarOpen ? "left-close" : "left-open"' in app
    assert 'rightSurfaceOpen ? "right-swap" : "right-open"' in app

    assert 'data-loom-layout-intent="left-open"' in css
    assert 'data-loom-layout-intent="right-close"' in css
    assert "--loom-readable-out-scale" in css
    assert "--loom-readable-in-scale" in css
    assert "scale(var(--loom-readable-out-scale,.985))" in css
    assert "scale(var(--loom-readable-in-scale,.98))" in css
    assert "104ms both" in css


def test_panel_surfaces_share_the_same_depth_language() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "@keyframes loom-panel-surface-in-left" in css
    assert "@keyframes loom-panel-surface-in-right" in css
    assert "@keyframes loom-right-surface-in" in css
    assert "scale(.985)" in css
    assert "scale(.976)" in css

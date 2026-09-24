from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_a61_panel_surface_motion_is_scoped_not_global() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert 'import { flushSync } from "react-dom"' in app
    assert "startViewTransition" in app
    assert "transition.updateCallbackDone" in app
    assert "runLayoutTransition" in app
    assert "layoutViewTransitionRef.current?.skipTransition()" in app
    assert 'document.documentElement.dataset.loomLayoutTransition = "true"' in app
    assert 'document.documentElement.dataset.loomLayoutIntent = intent' in app

    assert 'html[data-loom-layout-transition="true"] {' in css
    assert "view-transition-name: none" in css
    assert "view-transition-name: loom-sidebar" in css
    assert "view-transition-name: loom-inspector" in css
    assert "view-transition-name: loom-workspace" not in css
    assert "data-loom-layout-capture" not in css


def test_relocating_live_elements_use_flip_instead_of_disappear_reappear() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "captureLayoutAnchors" in app
    assert "animateLayoutAnchors" in app
    assert 'selector: ".thread-header-leading"' in app
    assert 'selector: ".thread-header-copy"' in app
    assert 'selector: ".polished-thread-header-actions"' in app
    assert 'selector: ".conversation-stage"' in app
    assert 'selector: ".composer-stage"' in app
    assert 'animation.id = "loom-layout-anchor"' in app
    assert "animation.commitStyles()" in app

    assert "loom-readable" not in css
    assert "loom-transcript-old" not in css
    assert "loom-composer-old" not in css
    assert "loom-header-copy-old" not in css


def test_a61_panel_depth_language_is_preserved_exactly() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "animation: loom-panel-surface-in-left 292ms cubic-bezier(.16,.78,.18,1) 72ms both" in css
    assert "animation: loom-panel-surface-out-left 78ms cubic-bezier(.42,0,.72,.2) both" in css
    assert "animation: loom-panel-surface-in-right 292ms cubic-bezier(.16,.78,.18,1) 72ms both" in css
    assert "animation: loom-panel-surface-out-right 78ms cubic-bezier(.42,0,.72,.2) both" in css
    assert "scale(.99)" in css
    assert "scale(.994)" in css

    assert "animation: loom-right-surface-out 92ms cubic-bezier(.42,0,.72,.2) both" in css
    assert "animation: loom-right-surface-in 286ms cubic-bezier(.16,.78,.18,1) 88ms both" in css
    assert "scale(.988)" in css


def test_unaffected_surfaces_are_not_snapshotted_during_opposite_side_motion() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert '[data-loom-layout-intent="left-open"]' in css
    assert '[data-loom-layout-intent="left-close"]' in css
    assert '[data-loom-layout-intent="right-open"]' in css
    assert '[data-loom-layout-intent="right-close"]' in css
    assert '[data-loom-layout-intent="right-swap"]' in css
    assert ".workspace-panels > .sidebar" in css
    assert ".workspace-panels > .inspector" in css


def test_inspector_micro_motion_is_frozen_during_panel_handoff() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "body.loom-panel-motion .workspace-panels > .inspector .runtime-pane" in css
    assert "animation: none !important" in css
    assert ".runtime-orbit-dot" in css
    assert ".runtime-orbit-one::before" in css
    assert ".runtime-orbit-two::before" in css
    assert "animation-play-state: paused !important" in css


def test_portal_surfaces_capture_only_their_logical_endpoint() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")
    review = read("desktop-react/src/components/ReviewWorkspace.tsx")
    agents = read("desktop-react/src/components/SubAgentDock.tsx")
    project = read("desktop-react/src/components/ProjectDetailsPanel.tsx")

    assert 'data-open={open ? "true" : "false"}' in review
    assert 'data-open={open ? "true" : "false"}' in agents
    assert 'data-open={open ? "true" : "false"}' in project
    assert '.review-workspace[data-open="true"]' in css
    assert '.review-workspace[data-open="false"]' in css

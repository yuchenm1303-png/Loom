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

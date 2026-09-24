from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_tracks_have_one_direct_layout_source_of_truth() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")
    app = read("desktop-react/src/App.tsx")

    assert "transition: grid-template-columns" not in css
    assert ".sidebar-layout-closed" in css
    assert ".inspector-layout-closed" in css
    assert app.count("const sidebarLayoutOpen = sidebarOpen") == 1
    assert "const inspectorLayoutOpen = inspectorVisible" in app
    assert "const reviewLayoutOpen = reviewOpen" in app
    assert "const agentsLayoutOpen = agentsOpen" in app
    assert "const projectDetailsLayoutOpen = projectDetailsOpen" in app


def test_inspector_content_survives_the_shared_exit_lifetime() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "const inspectorPresence = useMotionPresence(inspectorVisible, 420)" in app
    assert "items={inspectorPresence.mounted ? loom.items : EMPTY_TRANSCRIPT_ITEMS}" in app


def test_transcript_reanchors_once_when_final_layout_commits() -> None:
    app = read("desktop-react/src/App.tsx")
    scroll = read("desktop-react/src/components/TranscriptScrollController.tsx")

    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in app
    assert 'new Event(PANEL_LAYOUT_COMMIT_EVENT)' in app
    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in scroll
    assert "onPanelLayoutCommit" in scroll
    assert "scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)" in scroll


def test_visible_panel_actions_share_the_same_live_motion_coordinator() -> None:
    app = read("desktop-react/src/App.tsx")

    assert 'onToggleSidebar={() => runLayoutTransition(() => setSidebarOpen((open) => !open))}' in app
    assert 'onClose={() => runLayoutTransition(() => setInspectorOpen(false))}' in app
    assert 'onClose={() => runLayoutTransition(() => setReviewOpen(false))}' in app
    assert 'onClose={() => runLayoutTransition(() => setAgentsOpen(false))}' in app
    assert "startViewTransition" in app
    assert 'dataset.loomPanelSnapshot = "true"' in app


def test_layout_motion_does_not_introduce_vertical_stage_travel() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "centerY" not in app
    assert "deltaY" not in app
    assert 'translate3d(${deltaX}px,0,0)' in app


def test_panel_snapshot_never_captures_the_workspace_root() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert 'html[data-loom-panel-snapshot="true"] {' in css
    assert "view-transition-name: none" in css
    assert "view-transition-name: loom-sidebar" in css
    assert "view-transition-name: loom-inspector" in css
    assert "view-transition-name: loom-workspace" not in css
    assert "loom-transcript-old" not in css

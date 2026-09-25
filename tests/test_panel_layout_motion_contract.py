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


def test_inspector_content_stays_warm_across_panel_toggles() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "const inspectorPresence = useMotionPresence(inspectorVisible, 420)" in app
    assert "items={loom.items}" in app
    assert "items={inspectorPresence.mounted ? loom.items : EMPTY_TRANSCRIPT_ITEMS}" not in app


def test_transcript_reanchors_once_when_final_layout_commits() -> None:
    app = read("desktop-react/src/App.tsx")
    scroll = read("desktop-react/src/components/TranscriptScrollController.tsx")

    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in app
    assert 'new Event(PANEL_LAYOUT_COMMIT_EVENT)' in app
    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in scroll
    assert "onPanelLayoutCommit" in scroll
    assert "scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)" in scroll


def test_every_visible_panel_close_path_uses_the_same_coordinator() -> None:
    app = read("desktop-react/src/App.tsx")

    assert 'onToggleSidebar={() => runLayoutTransition(' in app
    assert 'sidebarOpen ? "left-close" : "left-open"' in app
    assert 'onClose={() => runLayoutTransition(() => setInspectorOpen(false), "right-close")}' in app
    assert 'onClose={() => runLayoutTransition(() => setReviewOpen(false), "right-close")}' in app
    assert 'onClose={() => runLayoutTransition(() => setAgentsOpen(false), "right-close")}' in app


def test_direction_metadata_is_cleaned_after_every_transition() -> None:
    app = read("desktop-react/src/App.tsx")

    assert 'delete document.documentElement.dataset.loomLayoutIntent' in app
    assert app.count('delete document.documentElement.dataset.loomLayoutIntent') >= 3


def test_layout_motion_moves_live_anchors_without_snapshotting_workspace() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "captureLayoutAnchors" in app
    assert "animateLayoutAnchors" in app
    assert 'selector: ".transcript"' in app
    assert 'selector: ".composer"' in app
    assert "deltaY" not in app
    assert "transition.updateCallbackDone" in app
    assert "view-transition-name: loom-workspace" not in css
    assert "loom-readable" not in css


def test_side_panel_close_has_monotonic_live_motion_contract() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "anchorXForIntent" in app
    assert "capture.leftX" in app
    assert "capture.rightX" in app
    assert "animateLayoutAnchors(captures, intent)" in app
    assert 'element.style.removeProperty("translate")' in app
    assert 'element.style.removeProperty("transform")' not in app


def test_live_workspace_uses_same_clock_as_a61_panel_group() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "const duration = 390;" in app
    assert 'easing: "cubic-bezier(.22,.7,.18,1)"' in app
    assert "case \"header-leading\": return 230" not in app
    assert "case \"composer\": return 285" not in app

    assert "animation-duration: var(--loom-motion-panel,390ms)" in css
    assert "animation-timing-function: var(--loom-ease-standard,cubic-bezier(.22,.7,.18,1))" in css

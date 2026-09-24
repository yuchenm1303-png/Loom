from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_tracks_commit_once_instead_of_interpolating_chat_width() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")
    app = read("desktop-react/src/App.tsx")

    assert "transition: grid-template-columns" not in css
    assert ".sidebar-layout-closed" in css
    assert ".inspector-layout-closed" in css
    assert "sidebarLayoutOpen" in app
    assert "inspectorLayoutOpen" in app
    assert "PANEL_LAYOUT_SETTLE_MS = 390" in app
    assert "useLinkedPanelMotion" in app


def test_inspector_keeps_content_until_overlay_slide_out_finishes() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "items={inspectorPresence.mounted ? loom.items : EMPTY_TRANSCRIPT_ITEMS}" in app
    assert "const inspectorPresence = useMotionPresence(inspectorVisible, PANEL_LAYOUT_SETTLE_MS + 30)" in app


def test_transcript_reanchors_once_when_panel_layout_commits() -> None:
    app = read("desktop-react/src/App.tsx")
    scroll = read("desktop-react/src/components/TranscriptScrollController.tsx")

    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in app
    assert 'new Event(PANEL_LAYOUT_COMMIT_EVENT)' in app
    assert 'PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit"' in scroll
    assert "onPanelLayoutCommit" in scroll
    assert "scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)" in scroll

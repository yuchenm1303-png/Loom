from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_settings_overlay_keeps_conversation_tree_mounted() -> None:
    app = read("desktop-react/src/App.tsx")
    motion = read("desktop-react/src/global-motion.css")

    assert "const settingsPresence = useMotionPresence(settingsOpen" in app
    assert 'if (settingsOpen) {' not in app
    assert 'className="settings-host"' in app
    assert "is-settings-obscured" in app
    assert ".settings-host[data-motion-phase=\"exiting\"]" in motion


def test_transient_surfaces_have_symmetric_presence() -> None:
    account = read("desktop-react/src/components/AccountDialog.tsx")
    composer = read("desktop-react/src/components/ComposerBase.tsx")
    review = read("desktop-react/src/components/ReviewWorkspace.tsx")
    agents = read("desktop-react/src/components/SubAgentDock.tsx")
    project = read("desktop-react/src/components/ProjectDetailsPanel.tsx")

    assert "useMotionPresence(open, 235)" in account
    assert "data-motion-phase={presence.phase}" in account
    assert "panelPresence = useMotionPresence(Boolean(openPanel), 215)" in composer
    assert composer.count("data-motion-phase={panelPresence.phase}") >= 3
    assert "useMotionPresence(open, 420)" in review
    assert "useMotionPresence(open, 420)" in agents
    assert "useMotionPresence(open && Boolean(projectProp), 420)" in project


def test_sidebar_disclosures_do_not_use_display_none_for_projects() -> None:
    sidebar = read("desktop-react/src/components/Sidebar.tsx")
    css = read("desktop-react/src/components/sidebar.css")

    assert "project-thread-list-shell" in sidebar
    assert "projectThreads.length && !collapsed" not in sidebar
    assert "grid-template-rows: 0fr" in css
    assert "thread-menu-out" in css
    assert "blur(18px) saturate(115%)" not in css


def test_panel_motion_suspends_stream_scroll_work() -> None:
    app = read("desktop-react/src/App.tsx")
    scroll = read("desktop-react/src/components/TranscriptScrollController.tsx")

    assert 'document.body.classList.add("loom-panel-motion")' in app
    assert 'document.body.classList.remove("loom-panel-motion")' in app
    assert 'document.body.classList.contains("loom-panel-motion")' in scroll
    assert 'window.dispatchEvent(new Event("loom:panel-resize-end"))' in app


def test_global_motion_uses_shared_tokens_and_double_frame_presence() -> None:
    motion = read("desktop-react/src/global-motion.css")
    presence = read("desktop-react/src/motion/useMotionPresence.ts")

    assert "--loom-motion-popover: 310ms" in motion
    assert "--loom-motion-dialog: 380ms" in motion
    assert "--loom-motion-panel: 390ms" in motion
    assert presence.count("requestAnimationFrame") >= 2

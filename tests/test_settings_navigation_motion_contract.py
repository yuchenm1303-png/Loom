from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PAGE = ROOT / "desktop-react" / "src" / "components" / "SettingsPage.tsx"
SETTINGS_MOTION = ROOT / "desktop-react" / "src" / "components" / "settings-page-motion.css"
GLOBAL_MOTION = ROOT / "desktop-react" / "src" / "global-motion.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_settings_navigation_has_no_deliberate_exit_stall_or_sync_render() -> None:
    source = read(SETTINGS_PAGE)

    assert 'from "react-dom"' not in source
    assert "flushSync" not in source
    assert "SETTINGS_SECTION_EXIT_MS" not in source
    assert "navigationTimerRef" not in source
    assert "window.setTimeout" not in source[source.index("const navigateToPage"):source.index("useEffect(() => () =>", source.index("const navigateToPage"))]


def test_settings_destination_commits_immediately_on_one_persistent_surface() -> None:
    source = read(SETTINGS_PAGE)
    nav = source[source.index("const navigateToPage"):source.index("useEffect(() => () =>", source.index("const navigateToPage"))]

    assert "setPage(nextPage);" in nav
    assert "setSettingsRoute(nextPage);" in nav
    assert 'setPageMotion(direction === "forward" ? "entering-forward" : "entering-backward");' in nav
    assert "requestAnimationFrame" in nav
    assert 'className="settings-page-surface" key={page}' not in source
    assert 'className="settings-page-surface" data-page={page}' in source
    assert 'className={page === item.key ? "active" : ""}' in source


def test_settings_page_motion_only_animates_the_compositor_surface() -> None:
    css = read(SETTINGS_MOTION)

    assert 'data-page-motion="leaving-forward"' not in css
    assert 'data-page-motion="leaving-backward"' not in css
    assert ".settings-page-surface > .settings-section" not in css
    assert ".memory-settings-page > .settings-section" not in css
    assert "translate3d(7px, 0, 0)" in css
    assert "translate3d(-7px, 0, 0)" in css
    assert "box-shadow 180ms" not in css


def test_global_motion_no_longer_competes_with_settings_section_motion() -> None:
    css = read(GLOBAL_MOTION)

    assert "Settings section navigation" not in css
    assert '.settings-page-surface[data-page-motion="leaving-forward"]' not in css
    assert '.settings-page-surface[data-page-motion="entering-forward"]' not in css

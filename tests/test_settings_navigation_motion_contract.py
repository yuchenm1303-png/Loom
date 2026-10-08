"""Contracts for how the Settings pages change.

The destination commits at once and the page rises in as a short cascade, with
the sidebar highlight travelling the same way. Users once found page switches
laggy on Windows high-DPI, so these tests pin the parts that keep it cheap:
nothing waits, nothing re-renders while it plays, only transform/opacity move,
only the page's top-level blocks animate, and the JavaScript stays out of the
click task.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PAGE = ROOT / "desktop-react" / "src" / "components" / "SettingsPage.tsx"
SETTINGS_MOTION = ROOT / "desktop-react" / "src" / "components" / "settings-page-motion.css"
SETTINGS_MOTION_JS = ROOT / "desktop-react" / "src" / "components" / "settingsMotion.ts"
GLOBAL_MOTION = ROOT / "desktop-react" / "src" / "global-motion.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def navigate_to_page(source: str) -> str:
    start = source.index("const navigateToPage")
    return source[start:source.index("useLayoutEffect(", start)]


def keyframes(css: str, name: str) -> str:
    start = css.index(f"@keyframes {name} " + "{")
    depth = 0
    for index in range(css.index("{", start), len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            return css[start:index + 1]
    raise AssertionError(f"unterminated @keyframes {name}")


def rule(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start:css.index("}", start) + 1]


def test_settings_navigation_has_no_deliberate_exit_stall_or_sync_render() -> None:
    source = read(SETTINGS_PAGE)

    assert 'from "react-dom"' not in source
    assert "flushSync" not in source
    assert "SETTINGS_SECTION_EXIT_MS" not in source
    assert "navigationTimerRef" not in source
    nav = navigate_to_page(source)
    assert "window.setTimeout" not in nav
    # Nothing is scheduled from the click either: no frames, no state flips.
    assert "requestAnimationFrame" not in nav
    assert "setPageMotion" not in source and "pageMotion" not in source


def test_settings_destination_commits_immediately_on_one_persistent_surface() -> None:
    source = read(SETTINGS_PAGE)
    nav = navigate_to_page(source)

    assert "setPage(nextPage);" in nav
    assert "setSettingsRoute(nextPage);" in nav
    # The surface itself never remounts; only its content is keyed by page, so
    # every page mounts fresh (no thumbs sliding between two pages' controls, no
    # stale transitions) and the entrance always has new nodes to play on.
    assert 'className="settings-page-surface" key={page}' not in source
    assert 'className="settings-page-surface" data-page={page} ref={surfaceRef}><Fragment key={page}>{content}</Fragment></div>' in source
    assert 'className={page === item.key ? "active" : ""}' in source
    assert "data-nav={item.key}" in source
    # The old state machine (entering/idle flips two frames apart) is gone.
    assert "data-page-motion" not in source and "SettingsPageMotion" not in source


def test_page_direction_follows_the_sidebar_not_the_route_table() -> None:
    source = read(SETTINGS_PAGE)
    nav = navigate_to_page(source)

    assert "PAGE_ORDER" not in source
    assert "const NAV_ORDER: PageKey[] = NAV_GROUPS.flatMap(" in source
    assert "const travel = prepareNavTravel(navRef.current, nextPage);" in nav
    # Direction comes from where the highlight travels; the sidebar order only
    # decides when the destination row is not on screen (a filtered sidebar).
    assert "const forward = travel !== 0 ? travel > 0 : NAV_ORDER.indexOf(nextPage) > NAV_ORDER.indexOf(page);" in nav


def test_navigation_effect_costs_the_click_nothing() -> None:
    page = read(SETTINGS_PAGE)
    motion = read(SETTINGS_MOTION_JS)

    effect_start = page.index("useLayoutEffect(() => {\n    const surface = surfaceRef.current;")
    effect = page[effect_start:page.index("}, [page]);", effect_start)]
    assert "reducedMotionPreferred()" in effect
    assert "return playPageTransition(surface, navRef.current, pending.flow, pending.travel);" in effect

    # Everything that reads styles or starts animations waits for the next frame,
    # where the browser has to resolve those styles anyway.
    transition = motion[motion.index("export function playPageTransition"):]
    assert "requestAnimationFrame(() => {" in transition
    assert transition.index("requestAnimationFrame(") < transition.index("playNavPillTravel(")
    assert transition.index("requestAnimationFrame(") < transition.index("startPageFlow(")
    assert "cancelAnimationFrame(frame)" in transition and "window.clearTimeout(timer)" in transition
    # No forced style recalcs, no synchronous rendering.
    assert "getComputedStyle" not in motion and "flushSync" not in motion
    # Layout is read at click time (clean) and once in the frame; nowhere else.
    before_frame = motion[:motion.index("function flowBlocks")]
    assert before_frame.count("getBoundingClientRect") == 2 and "prepareNavTravel" in before_frame


def test_page_marks_are_transient_and_late_blocks_join_the_cascade() -> None:
    motion = read(SETTINGS_MOTION_JS)

    assert "export const PAGE_FLOW_MS = 900;" in motion
    ladder = [int(value) for value in re.search(r"PAGE_FLOW_LADDER_MS = \[([\d, ]+)\]", motion).group(1).split(",")]
    assert ladder == sorted(ladder) and ladder[0] == 0
    # The last block starts within ~200 ms, so the whole cascade is over well
    # before the marks come down.
    assert ladder[-1] <= 200
    assert ladder[-1] + 470 < 900

    # The marks come off again, so sections that appear later never replay it.
    for cleanup in (
        "delete surface.dataset.flow;",
        "delete block.dataset.flowBlock;",
        'block.style.removeProperty("--flow-delay");',
        'block.style.removeProperty("--flow-distance");',
        "late.disconnect();",
    ):
        assert cleanup in motion
    # Blocks that mount a beat after the page (the portaled update card, a list
    # that has to load) are caught for as long as the marks are up: direct
    # children of the container only, never the whole subtree.
    assert "late.observe(container, { childList: true });" in motion
    assert "subtree" not in motion
    # A block that is late by more than its slot starts at once; one that is too
    # late to finish before the marks come down is left alone.
    assert "Math.max(0, delayFor(marked.size) - elapsed)" in motion
    assert "const LATE_BLOCK_WINDOW_MS = PAGE_FLOW_MS - 520;" in motion
    assert "if (elapsed > LATE_BLOCK_WINDOW_MS) return;" in motion
    # Pages that wrap everything in one container (models, memory, web search,
    # connectors) animate that container's children, not the wrapper.
    assert 'top.length === 1 && !top[0].classList.contains("settings-page-heading")' in motion
    # Off-screen and hidden blocks do not take part (a compositor layer each).
    assert "getBoundingClientRect().height > 0" in motion
    assert "getBoundingClientRect().top < bottom" in motion


def test_page_motion_only_animates_top_level_blocks_with_compositor_properties() -> None:
    css = read(SETTINGS_MOTION)

    # The block animation rides marks placed by script, never a deeper selector
    # (no per-card, per-row, per-control mount animation).
    assert '.settings-page-surface[data-flow] [data-flow-block] {' in css
    assert ".settings-page-surface > .settings-section" not in css
    assert ".memory-settings-page > .settings-section" not in css
    assert "data-page-motion" not in css
    assert 'data-page-motion="leaving-forward"' not in css

    frames = keyframes(css, "settings-block-fade")
    body = re.sub(r"@keyframes[^{]+", "", frames)
    assert set(re.findall(r"([a-z-]+)\s*:", body)) == {"opacity"}
    assert "@keyframes settings-block-rise" not in css
    assert "@keyframes settings-block-fall" not in css

    # Compositor-only, once, short.
    for forbidden in ("filter:", "backdrop-filter:", "will-change", "infinite", "box-shadow 180ms"):
        assert forbidden not in css, forbidden
    assert int(re.search(r"--flow-fade: (\d+)ms", css).group(1)) <= 260
    assert int(re.search(r"--flow-move: (\d+)ms", css).group(1)) <= 520
    # Page content is clipped without stretching the scrollport.
    assert "contain: paint style;" in rule(css, ".settings-shell.settings-refined .settings-page-surface")


def test_per_block_properties_are_registered_as_non_inherited() -> None:
    css = read(SETTINGS_MOTION)

    # Written inline on every block in the navigation frame. If they inherited,
    # every descendant of every block would be restyled for it.
    for name, syntax in (("--flow-delay", "<time>"), ("--flow-distance", "<length>")):
        block = css[css.index(f"@property {name} " + "{"):]
        block = block[:block.index("}")]
        assert f'syntax: "{syntax}";' in block and "inherits: false;" in block

    motion = read(SETTINGS_MOTION_JS)
    assert 'block.style.setProperty("--flow-delay"' in motion
    assert 'block.style.setProperty("--flow-distance", distance);' in motion
    assert 'surface.style.setProperty("--flow-distance"' not in motion


def test_the_update_card_never_flashes_on_another_page() -> None:
    css = read(SETTINGS_MOTION)

    # The card is portaled into General and React removes it a frame or two after
    # the page changes; until then it must not sit above the new page.
    assert '.settings-page-surface:not([data-page="general"]) > .software-update-section {\n  display: none;' in css
    assert 'node.classList.contains("software-update-section")' in read(SETTINGS_MOTION_JS)


def test_sidebar_highlight_is_one_text_free_pill_that_passes_under_the_labels() -> None:
    css = read(SETTINGS_MOTION)
    motion = read(SETTINGS_MOTION_JS)

    # The active row sits below the others inside the nav's own stacking context,
    # so its pill travels behind every label instead of wiping them out.
    assert "isolation: isolate;" in rule(css, ".settings-shell.settings-refined .settings-nav")
    assert "z-index: -1;" in rule(css, ".settings-shell.settings-refined .settings-nav button.active")
    # The old pill is replaced on the spot; two pills at once looked like a smear.
    pip = rule(css, ".settings-shell.settings-refined .settings-nav-pip")
    assert "opacity: 0;" in pip and "transition: none;" in pip

    playing = motion[motion.index("function playNavPillTravel"):motion.index("export function playPageTransition")]
    # Individual transform properties on a text-free element: translate carries it,
    # scale stretches it, each on its own animation and curve.
    assert "{ translate: [`0 ${-travel}px`, \"0 0\"] }" in playing
    assert "{ scale: [\"1 1\", `1 ${1 + stretch}`, \"1 1\"], offset: [0, 0.35, 1] }" in playing
    assert 'pseudoElement: "::before"' in playing
    for layout_property in ("top:", "left:", "height:", "width:", "margin"):
        assert layout_property not in playing
    # A second click while the pill is still gliding carries on from mid-air.
    assert '.querySelector<HTMLElement>(".settings-nav-pip")' in motion
    assert "(to.top + to.height / 2) - (from.top + from.height / 2)" in motion


def test_new_row_is_selected_only_when_the_pill_arrives() -> None:
    css = read(SETTINGS_MOTION)
    motion = read(SETTINGS_MOTION_JS)

    # Set at click time, before any style recalc can see `.active`; transitions
    # read their timing from the state they head to, so only activation waits.
    assert 'toRow.style.setProperty("--nav-arrive"' in motion
    assert 'row.style.removeProperty("--nav-arrive")' in motion
    delayed = css[css.index(".settings-nav button.active,\n"):]
    delayed = delayed[:delayed.index("}")]
    assert "transition-delay: var(--nav-arrive, 0ms) !important;" in delayed
    assert "> svg" in delayed and "> span:not(.settings-nav-pip)" in delayed


def test_reduced_motion_removes_every_animated_selector() -> None:
    css = read(SETTINGS_MOTION)
    page = read(SETTINGS_PAGE)
    motion = read(SETTINGS_MOTION_JS)

    media = css[css.index("@media (prefers-reduced-motion: reduce)"):css.index('html[data-loom-reduced-motion="true"]')]
    attribute = css[css.index('html[data-loom-reduced-motion="true"]'):]
    for selector in (
        ".settings-page-surface[data-flow] [data-flow-block]",
        ".settings-page-surface[data-flow] .settings-page-heading .settings-eyebrow::before",
        ".settings-nav button",
        ".settings-nav-pip",
        ".settings-nav-pip::before",
    ):
        assert selector + "," in media or selector + " {" in media, selector
        assert selector + "," in attribute or selector + " {" in attribute, selector
    assert "animation: none !important;" in media and "animation: none !important;" in attribute

    # Script side: no marks, no pill animation, no arrival delay.
    assert "!pending || reducedMotionPreferred()" in page
    assert "!reducedMotionPreferred()" in motion[motion.index("export function prepareNavTravel"):]
    assert 'document.documentElement.dataset.loomReducedMotion === "true"' in motion
    assert '(prefers-reduced-motion: reduce)' in motion


def test_global_motion_no_longer_competes_with_settings_section_motion() -> None:
    css = read(GLOBAL_MOTION)

    assert "Settings section navigation" not in css
    assert '.settings-page-surface[data-page-motion="leaving-forward"]' not in css
    assert '.settings-page-surface[data-page-motion="entering-forward"]' not in css

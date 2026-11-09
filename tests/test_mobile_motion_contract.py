"""The phone's motion has one owner.

Motion used to be scattered through mobile.css, with its own durations and curves, and entrances that only exist on a
current Chrome. A phone's WebView can be years behind: anything written with @starting-style or a bare linear() simply
did not animate there. These tests read the sources as text, like the other source contracts.
"""

import re
from pathlib import Path

MOBILE = Path(__file__).resolve().parents[1] / "desktop-react" / "src" / "mobile"


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def layout() -> str:
    return strip_comments((MOBILE / "mobile.css").read_text(encoding="utf-8"))


def motion() -> str:
    return strip_comments((MOBILE / "mobile-motion.css").read_text(encoding="utf-8"))


def test_the_layout_sheet_says_nothing_about_motion_and_pulls_the_motion_sheet_in() -> None:
    css = layout()
    assert not re.search(r"(?<![\w-])(transition|animation)(-[a-z-]+)?\s*:", css)
    assert "@keyframes" not in css
    assert '@import "./mobile-motion.css";' in (MOBILE / "mobile.css").read_text(encoding="utf-8").splitlines()[0]


def test_entrances_do_not_need_a_current_browser() -> None:
    css = motion() + layout()
    assert "@starting-style" not in css
    # A spring is always a variable with an ordinary curve behind it, for a WebView without linear().
    fallback = re.search(r'html\[data-loom-surface="mobile"\] \{(.*?)\}', motion(), re.S).group(1)
    for name in ("settle", "lively", "soft"):
        assert f"--m-{name}: var(--loom-ease-out);" in fallback
    for name in ("settle", "soft", "lively", "pop"):
        outside = re.sub(r"@supports[^{]*\{.*?\}\s*\}", "", motion(), flags=re.S)
        assert f"var(--loom-spring-{name})" not in outside, name


def test_a_surface_that_slides_starts_from_its_data_from_state() -> None:
    css = motion()
    for selector in (r"\.m-screen\[data-from\]", r"\.m-sheet-host\[data-from\] > \.m-sheet", r"\.m-motion-row\[data-from\]"):
        assert re.search(selector + r"[^{]*\{[^}]*transition: none", css), selector
    source = (MOBILE / "MobileApp.tsx").read_text(encoding="utf-8")
    assert "playEntrance(host.current)" in source and "playEntrance(screenElement.current)" in source


def test_list_and_screen_are_one_mover_both_ways() -> None:
    css = motion()
    def timing(selector: str) -> list[str]:
        body = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
        return re.findall(r"transform (var\([^)]+\)) (var\([^)]+\))", body)
    assert timing(".m-shell[data-covered]") == timing(".m-screen")
    assert timing(".m-shell") == timing('.m-screen[data-phase="exiting"]')


def test_tab_pages_stay_mounted_instead_of_being_replaced() -> None:
    source = (MOBILE / "MobileApp.tsx").read_text(encoding="utf-8")
    assert 'className="m-body" key={tab}' not in source
    assert "data-side={Math.sign(index - tabIndex)}" in source


def test_the_capsule_stretch_is_on_the_clock_of_its_travel() -> None:
    source = (MOBILE / "motion.ts").read_text(encoding="utf-8")
    assert "pill.animate(pillFrames, timing)" in source and "carrier.animate(carrierFrames, timing)" in source
    assert "cubic-bezier" not in source
    assert "cubic-bezier" not in (MOBILE / "NavCapsule.tsx").read_text(encoding="utf-8")
    assert ".animate(" not in (MOBILE / "BuildBranches.tsx").read_text(encoding="utf-8")

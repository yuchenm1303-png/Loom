"""How Loom's motion answers a hand.

test_motion_scale_contract.py keeps the scale (the steps and curves everything is written in). These keep the feel: an
exit moves on its first frame, a dock is a live surface that turns around where it is painted and leaves on the short
step, a surface mounts straight into its entrance, a press answers without moving words, nothing lifts under the pointer,
a class change never re-styles a whole subtree, and the loops that say "alive" keep one tempo.
docs/motion-feel-2026-10-21.md says what was measured and why. Like the other source contracts, these read the sources.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def stylesheets() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.css") if "node_modules" not in path.parts)


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def rules(css: str):
    """(chain of enclosing headers, selector, body, position) of every innermost rule."""
    out = []
    stack: list[tuple[str, int]] = []
    start = 0
    for index, char in enumerate(css):
        if char == "{":
            stack.append((css[start:index].strip(), index + 1))
            start = index + 1
        elif char == "}":
            if stack:
                header, body_start = stack.pop()
                body = css[body_start:index]
                if "{" not in body:
                    out.append(([h for h, _ in stack], " ".join(header.split()), body, body_start))
            start = index + 1
        elif char == ";" and not stack:
            start = index + 1
    return out


def declarations(body: str) -> dict[str, str]:
    found = {}
    for match in re.finditer(r"(?<![\w-])([\w-]+)\s*:\s*([^;]+)", body):
        found[match.group(1)] = re.sub(r"\s*!important\s*$", "", match.group(2)).strip()
    return found


def in_reduced_motion(headers: list[str], selector: str) -> bool:
    return any("reduced-motion" in header for header in headers) or "reduced-motion" in selector


def in_keyframes(headers: list[str]) -> bool:
    return any(header.startswith("@keyframes") for header in headers)


# ---- exits ---------------------------------------------------------------------------------------------------------

def test_every_exit_starts_on_its_first_frame() -> None:
    tokens = read("motion-tokens.css")
    exit_curve = re.search(r"--loom-ease-exit:\s*cubic-bezier\(([^)]*)\)", tokens).group(1)
    x1, y1, _x2, y2 = (float(part) for part in exit_curve.split(","))
    # It leaves at constant speed (the first control point on the diagonal) and lands softly.
    assert x1 == y1 and y2 == 1, exit_curve
    assert "--loom-ease-in:" not in tokens

    slow_starts = []
    for path in stylesheets():
        for headers, selector, body, _ in rules(strip_comments(path.read_text(encoding="utf-8"))):
            if in_keyframes(headers) or in_reduced_motion(headers, selector):
                continue
            for prop, value in declarations(body).items():
                if not re.match(r"(transition|animation)(-timing-function)?$", prop):
                    continue
                if re.search(r"(?<![\w-])ease-in(?![\w-])|--loom-ease-in\b", value):
                    slow_starts.append(f"{rel(path)}: {selector[:70]} {{ {prop}: {value[:60]} }}")
    assert not slow_starts, "\n".join(slow_starts)


# ---- docks ---------------------------------------------------------------------------------------------------------

def test_docks_are_live_surfaces_that_leave_on_the_short_step() -> None:
    app = read("App.tsx")
    panels = read("components/workspace-panels.css")
    language = read("components/panel-language.css")

    # Right-hand changes never wait for a snapshot (it had to paint and capture the old frame first). Only the sidebar
    # snapshots, and only its card and the two toolbar controls it shares with the header.
    assert 'const sidebarSnapshot = intent === "left-open" || intent === "left-close";' in app
    assert 'if (!sidebarSnapshot || typeof transitionDocument.startViewTransition !== "function")' in app
    named = set()
    for path in stylesheets():
        named |= set(re.findall(r"view-transition-name:\s*([\w-]+)", strip_comments(path.read_text(encoding="utf-8"))))
    assert named <= {"none", "loom-sidebar", "loom-sidebar-toggle", "loom-sidebar-compose"}, named

    # The sidebar and the inspector: readable on their first frame, settled on the panel clock, gone on fast-out.
    assert "opacity var(--loom-dur-fast) var(--loom-ease-out) calc(var(--loom-dur-fast) * -.2)" in panels
    assert "transform var(--loom-dur-panel) var(--loom-ease-out)" in panels
    closed = panels[panels.index(".workspace-panels.sidebar-closed > .sidebar,\n.workspace-panels.inspector-closed > .inspector {"):]
    closed = closed[:closed.index("}")]
    assert "opacity var(--loom-dur-fast-out) var(--loom-ease-exit)" in closed
    assert "transform var(--loom-dur-fast-out) var(--loom-ease-exit)" in closed
    assert "visibility 0s linear var(--loom-dur-fast-out)" in closed
    assert "position: absolute" in closed

    # The portal docks speak the same language through their presence.
    exiting = language[language.index('.pl-dock[data-motion-phase="exiting"] {'):]
    exiting = exiting[:exiting.index("}")]
    assert "transition-duration: var(--loom-dur-fast-out)" in exiting
    assert "transition-timing-function: var(--loom-ease-exit)" in exiting
    starting = language[language.index("@starting-style {\n  .pl-dock"):]
    assert "opacity: .72" in starting[:starting.index("}\n}")]
    # Nothing stretches a dock's exit onto the panel clock any more.
    assert "transition-duration: var(--loom-dur-panel) !important" not in panels

    for component in ("ReviewWorkspace.tsx", "SubAgentDock.tsx", "ArtifactPreviewDock.tsx", "ProjectDetailsPanel.tsx"):
        assert "RETAIN.fast)" in read(f"components/{component}"), component
    assert "useMotionPresence(inspectorVisible, RETAIN.fast)" in app


# ---- presence ------------------------------------------------------------------------------------------------------

def test_a_surface_mounts_straight_into_its_entrance() -> None:
    hook = read("motion/useMotionPresence.ts")
    assert 'phase: open ? "entered" : "exiting"' in hook
    # The exit's clock starts with the frame that starts the exit.
    assert hook.index("requestAnimationFrame(") < hook.index("window.setTimeout(")

    entering = [rel(path) for path in stylesheets() if 'motion-phase="entering"' in path.read_text(encoding="utf-8")]
    assert not entering, entering
    for source in SRC.rglob("*.tsx"):
        assert 'phase !== "entering"' not in source.read_text(encoding="utf-8"), source.name


def test_a_starting_style_is_not_outranked_by_the_rule_it_starts_from() -> None:
    # A starting style has no weight of its own: a normal rule with the same selector later in the file wins over it,
    # and the surface then appears at once instead of entering (the settings page did, for a while).
    late = []
    for path in stylesheets():
        found = rules(strip_comments(path.read_text(encoding="utf-8")))
        starting = [(selector, set(declarations(body)), position) for headers, selector, body, position in found
                    if headers and headers[-1].startswith("@starting-style")]
        for selector, props, position in starting:
            for headers, other, body, other_position in found:
                if other_position <= position or (headers and headers[-1].startswith("@starting-style")):
                    continue
                if other == selector and props & set(declarations(body)):
                    late.append(f"{rel(path)}: {selector[:90]}")
    assert not late, "\n".join(late)


# ---- press and hover -----------------------------------------------------------------------------------------------

# What may still move under a press: a switch's knob stretches (a text-free mark), the window draws its own pressed
# button, the avatar crop shows a grabbing hand, and icon-motion.css gives the glyph.
PRESS_EXCEPTIONS = re.compile(r"settings-switch|loom-window-controls|avatar-crop|-webkit-autofill")


def test_a_press_moves_no_words() -> None:
    press = read("motion/press.css")
    assert "background-image: linear-gradient(var(--loom-press-wash), var(--loom-press-wash))" in press
    assert "scale: var(--loom-press-glyph)" in read("components/icon-motion.css")

    offenders = []
    for path in stylesheets():
        if rel(path) in ("motion/press.css", "components/icon-motion.css"):
            continue
        for headers, selector, body, _ in rules(strip_comments(path.read_text(encoding="utf-8"))):
            if ":active" not in selector or in_reduced_motion(headers, selector) or PRESS_EXCEPTIONS.search(selector):
                continue
            for prop, value in declarations(body).items():
                if prop in ("transform", "translate", "scale") and value != "none":
                    offenders.append(f"{rel(path)}: {selector[:80]} {{ {prop}: {value} }}")
                if prop.startswith("background"):
                    offenders.append(f"{rel(path)}: {selector[:80]} {{ {prop}: {value[:40]} }} (the press wash is press.css's)")
    assert not offenders, "\n".join(offenders)


# Marks that may still lean under the pointer: glyphs and their parts, an arrow, a stop, a pseudo-element wash or glow.
HOVER_MARKS = re.compile(r"::|svg|icon|glyph|arrow|chevron|stop|track|glow|li-|file-open|open-hint|workspace-copy")


def test_nothing_that_carries_words_lifts_under_the_pointer() -> None:
    lifts = []
    for path in stylesheets():
        if rel(path) == "components/icon-motion.css":
            continue
        for headers, selector, body, _ in rules(strip_comments(path.read_text(encoding="utf-8"))):
            if ":hover" not in selector or in_keyframes(headers) or in_reduced_motion(headers, selector):
                continue
            for part in selector.split(","):
                subject = re.split(r"\s+|>|\+|~", part.strip())[-1]
                if ":hover" not in part or HOVER_MARKS.search(subject):
                    continue
                for prop, value in declarations(body).items():
                    if prop == "transform" and re.search(r"translate[XY]?\(", value):
                        lifts.append(f"{rel(path)}: {part.strip()[:80]} {{ transform: {value} }}")
                    if prop == "translate" and value != "none":
                        lifts.append(f"{rel(path)}: {part.strip()[:80]} {{ translate: {value} }}")
    assert not lifts, "\n".join(lifts)


# ---- what a class change costs -------------------------------------------------------------------------------------

def test_no_selector_reads_a_substring_of_the_class_attribute() -> None:
    # [class*="context"] [class*="track"] made every class change anywhere re-style the whole subtree below it.
    found = []
    for path in stylesheets():
        css = strip_comments(path.read_text(encoding="utf-8"))
        found += [f"{rel(path)}: {match.group(0)}" for match in re.finditer(r"\[class\s*[*^$~|]?=[^\]]*\]", css)]
    assert not found, "\n".join(found[:20])


def test_the_dock_tracks_stay_on_the_shell() -> None:
    # Inherited, a track that changes with every dock toggle re-styles every element of the app below the shell.
    panels = strip_comments(read("components/workspace-panels.css"))
    for name in ("--loom-sidebar-track", "--loom-inspector-track"):
        block = re.search(rf"@property {name}\s*\{{([^}}]*)\}}", panels)
        assert block and re.search(r"inherits:\s*false", block.group(1)), name
    # Nothing below the shell reads them.
    for path in stylesheets():
        css = strip_comments(path.read_text(encoding="utf-8"))
        for headers, selector, body, _ in rules(css):
            if re.search(r"var\(--loom-(sidebar|inspector)-track\)", body):
                assert selector.startswith(".app-shell.workspace-panels"), f"{rel(path)}: {selector}"


# ---- loops ---------------------------------------------------------------------------------------------------------

ALIVE = {
    "breathe": ["pl-pulse", "run-progress-pulse", "general-status-breathe", "compact-thread-pulse", "sub-agent-pulse",
                "task-plan-pulse", "header-state-breathe", "wv-breathe", "wv-halo", "status-breathe", "streaming-glow",
                "composer-status-breathe-refined", "runtime-pulse", "loom-tool-ping"],
    "shimmer": ["thinking-text-shimmer"],
    "skeleton": ["empty-startup-pulse", "home-token-pulse", "profile-skeleton", "compact-sidebar-skeleton-pulse"],
}


def test_the_loops_that_say_alive_keep_one_tempo() -> None:
    off = []
    seen = set()
    for path in stylesheets():
        for headers, selector, body, _ in rules(strip_comments(path.read_text(encoding="utf-8"))):
            if in_keyframes(headers) or in_reduced_motion(headers, selector):
                continue
            value = declarations(body).get("animation", "")
            for period, names in ALIVE.items():
                for name in names:
                    if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", value):
                        seen.add(name)
                        if f"var(--loom-period-{period})" not in value and "var(--thinking-cycle" not in value:
                            off.append(f"{rel(path)}: {selector[:60]} {{ animation: {value[:70]} }}")
    assert not off, "\n".join(off)
    assert seen >= {"pl-pulse", "wv-breathe", "thinking-text-shimmer", "profile-skeleton"}, seen
    # A running conversation's ring in the sidebar turns on the breathe period, not a loader's pace.
    assert "animation: li-spin var(--loom-period-breathe) linear infinite;" in read("components/sidebar.css")
    thinking = read("components/inline-thinking.css")
    assert "--thinking-cycle: var(--loom-period-shimmer);" in thinking

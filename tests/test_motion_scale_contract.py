"""The scale the renderer's motion is written in.

Before this there were 124 distinct durations and 58 curves in the stylesheets (twenty of them the same ease-out with
control points a few hundredths apart), eleven private token namespaces, a reduced-motion switch written two hundred times
and a hand-copied copy of every duration in the scripts. Two or three of them can be told apart on a screen; the rest is
noise a person feels as "assembled" rather than "designed". src/motion-tokens.css is now the one place where time and curves
are chosen (docs/motion-language-2026-10-18.md says how), src/motion/tokens.ts repeats its numbers for the code that needs
them, and these tests keep the scale from growing back. They read the sources as text, like the other source contracts.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
TOKENS_CSS = SRC / "motion-tokens.css"
TOKENS_TS = SRC / "motion" / "tokens.ts"

DURATIONS = {
    "press": 90,
    "fast": 150, "fast-out": 120,
    "base": 240, "base-out": 200,
    "flow": 320, "flow-out": 260,
    "panel": 390,
    "slow": 520,
}
CURVES = {
    "out": "cubic-bezier(.16, .78, .18, 1)",
    "move": "cubic-bezier(.22, .7, .18, 1)",
    "exit": "cubic-bezier(.3, .3, .55, 1)",
    "inout": "cubic-bezier(.4, 0, .2, 1)",
    "glide": "cubic-bezier(.2, 0, 0, 1)",
}

# Files whose numbers are physics, not interaction timings. Each has its own tests: the send hand-off
# (test_capsule_motion_contract, send-flight.test.mjs), the icon gestures (icon-motion.test.mjs), the pet, the soft pointer
# and the computer-use HUD, which are ornament. (The docks and the quick-tool dock used to be listed here; both are written
# on the scale now.)
PHYSICS = {
    "components/icon-motion.css",
    "components/send-flight.css",
    "loom-pet.css",
    "pointer-click.css",
    "pointer-motion.css",
    "components/computer-use-hud.css",
    "motion-tokens.css",
}
# One-shot accents that are not a step of the scale: a sweep of light, a ripple, a countdown, the send hand-off, the
# browser's autofill workaround. Everything else that runs once is on the scale.
ONE_SHOTS = {
    "rt-ripple", "rt-spark", "loom-block-glint", "composer-edge-glide-once", "context-meter-hover-sweep",
    "settings-toast-timer", "loom-user-send-entry", "loom-user-bubble-unfold", "loom-user-bubble-edge",
    "loom-user-send-media",
}
# Scripts whose numbers are physics (the flight, the pointer) or are not motion at all.
SCRIPT_PHYSICS = {"sendFlight.ts", "sendFlightMotion.ts", "pointerClick.ts", "fontWarmup.ts", "webBridge.ts"}

TIME = re.compile(r"^-?(\d*\.?\d+)(ms|s)$")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def stylesheets() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.css") if "node_modules" not in path.parts)


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def blocks(css: str):
    """(headers, body) of every innermost block, headers being the chain of selectors / at-rule preludes around it."""
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
                    out.append(([h for h, _ in stack] + [header], body))
            start = index + 1
        elif char == ";" and not stack:
            start = index + 1
    return out


def split_top(value: str, separator: str = ",") -> list[str]:
    parts, depth, current = [], 0, ""
    for char in value:
        depth += (char == "(") - (char == ")")
        if char == separator and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def tokens(item: str) -> list[str]:
    out, depth, current = [], 0, ""
    for char in item:
        depth += (char == "(") - (char == ")")
        if char.isspace() and depth == 0:
            if current:
                out.append(current)
            current = ""
        else:
            current += char
    if current:
        out.append(current)
    return out


def motion_declarations():
    """(file, header, property, value) of every transition / animation declaration outside reduced-motion rules."""
    for path in stylesheets():
        if rel(path) in PHYSICS:
            continue
        css = strip_comments(path.read_text(encoding="utf-8"))
        for headers, body in blocks(css):
            joined = " ".join(headers)
            if "prefers-reduced-motion" in joined or "data-loom-reduced-motion" in joined:
                continue
            for match in re.finditer(r"(?<![\w-])(transition|animation)(-duration|-timing-function)?\s*:\s*([^;]+)", body):
                yield rel(path), headers[-1], match.group(1) + (match.group(2) or ""), re.sub(r"\s*!important\s*$", "", match.group(3)).strip()


def ms(token: str) -> float:
    match = TIME.match(token)
    value = float(match.group(1))
    return value * 1000 if match.group(2) == "s" else value


# ---- the scale ----------------------------------------------------------------------------------------------------

def test_the_scale_is_written_once_and_the_scripts_repeat_it() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    script = TOKENS_TS.read_text(encoding="utf-8")

    declared = {name: int(value) for name, value in re.findall(r"--loom-dur-([\w-]+):\s*(\d+)ms;", css.split("@media")[0])}
    assert declared == DURATIONS
    assert re.search(r"--loom-stagger:\s*40ms;", css)
    curves = {name: re.sub(r"\s+", " ", value) for name, value in re.findall(r"--loom-ease-(\w+):\s*(cubic-bezier\([^)]*\));", css)}
    assert curves == CURVES

    # The scripts repeat the same numbers.
    dur = dict(re.findall(r"(\w+):\s*(\d+),", script[script.index("export const DUR = {"):script.index("} as const;")]))
    out = dict(re.findall(r"(\w+):\s*(\d+),", script[script.index("export const DUR_OUT = {"):script.index("} as const;", script.index("export const DUR_OUT"))]))
    assert {name: int(value) for name, value in dur.items()} == {k: v for k, v in DURATIONS.items() if "-" not in k}
    assert {name: int(value) for name, value in out.items()} == {k[:-4]: v for k, v in DURATIONS.items() if k.endswith("-out")}
    assert "STAGGER_MS = 40" in script
    ease = dict(re.findall(r"(\w+):\s*\"(cubic-bezier\([^)]*\))\"", script))
    assert {name: re.sub(r"\s+", " ", value) for name, value in ease.items()} == CURVES
    for name in ("settle", "soft", "lively", "pop"):
        in_css = re.search(rf"--loom-spring-{name}:\s*(linear\([^;]*\));", css).group(1)
        in_ts = re.search(rf"{name}:\s*\"(linear\([^\"]*\))\"", script).group(1)
        assert re.sub(r"\s+", " ", in_css) == re.sub(r"\s+", " ", in_ts), name


def test_how_long_a_surface_is_kept_after_it_closes_is_derived_from_its_exit() -> None:
    script = TOKENS_TS.read_text(encoding="utf-8")
    margin = int(re.search(r"RETAIN_MARGIN_MS = (\d+)", script).group(1))
    # The surface must still be painted for the whole of its exit, with a frame of slack at 60 Hz and more.
    assert margin >= 20
    retain = script[script.index("export const RETAIN = {"):script.index("} as const;", script.index("export const RETAIN"))]
    for name, formula in (
        ("fast", "DUR_OUT.fast + RETAIN_MARGIN_MS"), ("base", "DUR_OUT.base + RETAIN_MARGIN_MS"),
        ("flow", "DUR_OUT.flow + RETAIN_MARGIN_MS"), ("foldBase", "DUR.base + RETAIN_MARGIN_MS"),
        ("fold", "DUR.flow + RETAIN_MARGIN_MS"),
    ):
        assert f"{name}: {formula}," in retain, name
    assert "panel: DUR.panel + 30," in retain


def test_reduced_motion_collapses_the_clock() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    media = css[css.index("@media (prefers-reduced-motion: reduce)"):css.index(':root[data-loom-reduced-motion="true"]')]
    setting = css[css.index(':root[data-loom-reduced-motion="true"]'):]
    for block in (media, setting):
        for name in DURATIONS:
            assert re.search(rf"--loom-dur-{name}:\s*1ms;", block), name
        assert "--loom-stagger: 0ms;" in block


def test_the_scale_loads_before_every_stylesheet() -> None:
    cascade = [line for line in (SRC / "renderer-styles.ts").read_text(encoding="utf-8").splitlines() if line.startswith("import ")]
    assert cascade[0] == 'import "./motion-tokens.css";'


# ---- the stylesheets ----------------------------------------------------------------------------------------------

def test_no_stylesheet_writes_its_own_duration() -> None:
    off: list[str] = []
    for file, header, prop, value in motion_declarations():
        if prop.endswith("timing-function"):
            continue
        for item in split_top(value):
            parts = tokens(item)
            if item == "none" or "infinite" in parts:
                continue
            # The first time in an item is its duration, the second its delay (a delay is choreography, not a step).
            times = [part for part in parts if TIME.match(part) or part.startswith("var(--loom-dur-")]
            if not times or not TIME.match(times[0]):
                continue
            duration = times[0]
            if {part for part in parts if part in ONE_SHOTS} or ms(duration) <= 1:
                continue  # an accent that is not a step; a switch-off
            if prop == "animation-duration" and ms(duration) >= 1000:
                continue  # a loop's period, set where the loop is switched on
            if ms(duration) >= 5000:
                continue  # the browser's autofill workaround (background-color 9999s)
            off.append(f"{file}: {header[:50]} {prop}: {item[:80]}")
    assert not off, "\n".join(off[:25])


def test_no_stylesheet_invents_a_curve() -> None:
    off: list[str] = []
    for file, header, prop, value in motion_declarations():
        for item in split_top(value):
            if "infinite" in tokens(item):
                continue
            for literal in re.findall(r"cubic-bezier\([^)]*\)|(?<![\w-])linear\([^)]+\)", item):
                names = {part for part in tokens(item) if part in ONE_SHOTS}
                if not names:
                    off.append(f"{file}: {header[:50]} {prop}: {literal}")
    # The send hand-off's own curve (sendFlight.ts lands the capsule on it) is declared once, as a token of that file.
    assert not off, "\n".join(off[:25])


def test_the_only_curve_a_stylesheet_may_declare_is_the_send_hand_offs() -> None:
    declared = []
    for path in stylesheets():
        if rel(path) in PHYSICS:
            continue
        css = strip_comments(path.read_text(encoding="utf-8"))
        declared += [(rel(path), name) for name, value in re.findall(r"(--[\w-]+):\s*(cubic-bezier\([^)]*\))", css)]
    assert declared == [("components/generation-motion.css", "--loom-generation-pop")], declared


def test_a_fold_takes_a_fold_s_time() -> None:
    # Height is made by layout, so what is below glides. A fold of a few lines takes `base`, a block or a list `flow`,
    # the completion of a turn `panel`; nothing that changes the size of a thing is as quick as a hover.
    quick = []
    for file, header, prop, value in motion_declarations():
        if prop != "transition":
            continue
        for item in split_top(value):
            parts = tokens(item)
            if parts and parts[0] in ("grid-template-rows", "grid-template-columns") and re.search(r"var\(--loom-dur-(press|fast)", item):
                quick.append(f"{file}: {header[:50]}: {item[:80]}")
    assert not quick, "\n".join(quick)


# ---- text does not travel ------------------------------------------------------------------------------------------

# Entrances that move: the docks (a61), the marks (a glyph, a bead, a ring, a bar being drawn, a tick) and the send
# hand-off. Whatever carries words fades in; space is made by layout.
TRAVELLING = re.compile(
    r"^(loom-sticker-pop$|wv-(glyph|bead|rail|halo|ring)|pf-(node|meter|mark)-in$|run-progress-mark-in$|"
    r"profile-(bar|hour)-grow$|loom-task-icon-spring$|loom-reasoning-chevron-in$|task-plan-mark$|settings-eyebrow-draw$|"
    r"loom-user-|loom-flight-|li-|rt-|sidebar-brand|empty-loom|loom-pet|loom-hud|tg-|status-breathe|streaming-glow|dot-wave|"
    r"orbit-rotate|spark-float|ambient-drift|compact-thread-pulse|context-meter-|home-token-pulse|general-status-breathe|"
    r"sub-agent-pulse|runtime-|header-state-breathe|composer-|model-core-layer-in$|loom-count-tick$)"
)


def test_what_carries_words_fades_in_and_does_not_move() -> None:
    moving: list[str] = []
    for path in stylesheets():
        css = strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"@keyframes\s+([\w-]+)\s*\{", css):
            name = match.group(1)
            if not re.search(r"(-in|-enter|arrive|rise|reveal|-pop)$", name) or TRAVELLING.match(name):
                continue
            depth, index = 0, match.end() - 1
            for index in range(match.end() - 1, len(css)):
                depth += (css[index] == "{") - (css[index] == "}")
                if depth == 0:
                    break
            body = css[match.end():index]
            if re.search(r"(transform|translate|scale|rotate|filter)\s*:", body):
                moving.append(f"{rel(path)}: {name}")
    assert not moving, moving


# ---- the scripts --------------------------------------------------------------------------------------------------

def scripts():
    for path in sorted(SRC.rglob("*.ts*")):
        if path.name in SCRIPT_PHYSICS or "node_modules" in path.parts or path.suffix not in (".ts", ".tsx"):
            continue
        yield path, path.read_text(encoding="utf-8")


def test_scripts_take_their_durations_from_the_scale() -> None:
    off: list[str] = []
    for path, source in scripts():
        if path.name == "tokens.ts":
            continue
        for match in re.finditer(r"useMotionPresence\(([^()]|\([^()]*\))*?,\s*(\d+)\b", source):
            if int(match.group(2)) != 0:
                off.append(f"{path.name}: {match.group(0)[:80]}")
        for match in re.finditer(r"\bduration:\s*(\d+)\b", source):
            off.append(f"{path.name}: duration: {match.group(1)}")
        for match in re.finditer(r"easing:\s*\"(cubic-bezier|linear)\(", source):
            off.append(f"{path.name}: easing literal")
    assert not off, "\n".join(off)


def test_one_function_asks_whether_motion_is_reduced() -> None:
    # The in-app setting and the system's: motion/useReducedMotion.ts. A second copy is how one of them gets forgotten.
    copies = []
    for path, source in scripts():
        if path.name in ("useReducedMotion.ts", "main.tsx", "SettingsPage.tsx"):
            continue  # the hook itself, and the two places that write the setting
        if "dataset.loomReducedMotion" in source or "prefers-reduced-motion: reduce)\")" in source:
            copies.append(path.name)
    # The ornament that used to carry its own copy (the pet, the soft pointer, the follow loop, the lens, the theme
    # switch, the portal cursor) asks the hook now, and listens through onReducedMotionChange.
    assert copies == [], copies

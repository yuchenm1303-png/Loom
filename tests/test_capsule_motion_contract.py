"""Contracts for the live capsule motion language in the transcript.

Thinking capsules unroll from their bead. Task capsules inflate from a shallow
clip with their body lit from inside, so commands are readable from frame 0;
the same light returns in the outcome colour when a task settles and, softly,
when the user opens a group. Icons retain their spring motion. These tests pin
the parts that are easy to regress silently: text-bearing surfaces never
animate transforms, only the live anchor animates, and history never replays
motion.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
TRANSCRIPT = COMPONENTS / "Transcript.tsx"
MOTION = COMPONENTS / "conversation-motion.css"
GENERATION = COMPONENTS / "generation-motion.css"
THINKING = COMPONENTS / "inline-thinking.css"
THEME = SRC / "theme.css"

TRANSFORM_PROPERTY = re.compile(r"(?<![\w-])(transform|translate|scale|rotate)\s*:")

# Surfaces that paint task/thinking copy. Their entrances may clip or fade,
# but never move or scale: Windows/HiDPI renders softened glyphs otherwise.
TEXT_BEARING_SUBJECTS = (
    ".task-flow-group-header",
    ".task-flow-group-title",
    ".task-flow-verb",
    ".task-flow-row-wrap",
    ".task-flow-row",
    ".task-flow-row-main",
    ".inline-thinking",
    ".thinking-shimmer",
    ".live-reasoning-trigger",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def keyframes(source: str, name: str) -> str:
    start = source.index(f"@keyframes {name} {{")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated @keyframes {name}")


def rules(source: str) -> list[tuple[str, str]]:
    """Innermost `selector { body }` pairs, comments stripped."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return [(selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source)]


def subject(selector: str) -> str:
    return selector.split()[-1] if selector.split() else ""


def animation_names(body: str) -> list[str]:
    names: list[str] = []
    for declaration in re.findall(r"animation\s*:([^;]+)", body):
        # Keyframe names only; `var(--loom-...)` easing tokens are not animations.
        names.extend(re.findall(r"(?<![\w-])loom-[\w-]+", declaration))
    return names


def test_text_bearing_surfaces_never_animate_transforms() -> None:
    motion = read(MOTION)
    thinking = read(THINKING)
    checked: set[str] = set()

    for source in (motion, thinking):
        for selector_list, body in rules(source):
            for selector in selector_list.split(","):
                last = subject(selector)
                if "::" in last or not any(
                    last == name or last.startswith(name + ".") or last.startswith(name + ":")
                    for name in TEXT_BEARING_SUBJECTS
                ):
                    continue
                for name in animation_names(body):
                    frames = keyframes(motion if f"@keyframes {name} {{" in motion else thinking, name)
                    assert not TRANSFORM_PROPERTY.search(frames), (selector.strip(), name)
                    checked.add(name)

    # The walk must actually reach the capsule birth, not pass vacuously.
    assert {"loom-capsule-unroll", "loom-task-capsule-land", "loom-task-copy-in", "loom-task-verb-in"} <= checked
    assert "loom-reasoning-handoff" in checked


def test_capsule_unrolls_from_its_bead_through_clip_path_only() -> None:
    motion = read(MOTION)
    unroll = keyframes(motion, "loom-capsule-unroll")

    assert "opacity" not in unroll and not TRANSFORM_PROPERTY.search(unroll)
    # Point at the bead centre -> bead disc -> full capsule -> past the border
    # box, so the capsule's own shadow is revealed before the clip is released.
    assert "inset(50% calc(100% - (var(--capsule-bead-start" in unroll
    assert "inset(0 calc(100% - var(--capsule-bead-end, 32px)) 0 var(--capsule-bead-start, 0px) round 999px)" in unroll
    assert "inset(-32px -32px -32px -32px round 999px)" in unroll

    assert "--capsule-bead-end: 33px;" in motion
    assert "--capsule-bead-end: 43px;" in motion
    # Thinking still unrolls; task commands must be readable from first paint.
    assert "animation: loom-capsule-unroll" in read(THINKING)
    land = keyframes(motion, "loom-task-capsule-land")
    assert "clip-path" not in land and not TRANSFORM_PROPERTY.search(land)
    assert "opacity: 0;" not in land
    copy_rules = [body for selector, body in rules(motion)
                  if selector == ".turn-process.is-live .task-flow-group.is-running .task-flow-row-main"]
    assert len(copy_rules) == 1 and "animation: none;" in copy_rules[0]


def test_beads_pop_on_sampled_springs() -> None:
    motion = read(MOTION)

    pop = re.search(r"--loom-spring-pop: linear\(([^)]*)\);", motion)
    soft = re.search(r"--loom-spring-soft: linear\(([^)]*)\);", motion)
    assert pop and soft
    for curve, overshoot in ((pop, 1.05), (soft, 1.02)):
        points = [float(value) for value in curve.group(1).split(",")]
        assert points[0] == 0 and points[-1] == 1
        assert max(points) > overshoot

    spring = keyframes(motion, "loom-task-icon-spring")
    # Individual transform properties never clobber an element's own transform.
    assert "transform:" not in spring and "scale:" in spring and "rotate:" in spring
    assert "animation: loom-task-icon-spring 560ms var(--loom-spring-pop) backwards;" in motion


def test_only_the_live_anchor_group_animates() -> None:
    transcript = read(TRANSCRIPT)
    motion = read(MOTION)

    # A group is the live anchor while one of its rows is genuinely active, or
    # (between tool batches) while it is the latest group of a turn that is
    # still alive. Everything else is history.
    assert "const activeActivityBlocks = useMemo(() => {" in transcript
    assert "if (block.items.some((item) => isActiveActivityStatus(itemStatus(item)))) live.add(index);" in transcript
    assert "keepOpen={activeActivityBlocks.has(index) || continuingActivityBlock === index}" in transcript
    assert "keepOpen={keepActivityOpen}" not in transcript

    # Every birth/tick rule is scoped to the running anchor of a live turn, so
    # finished groups and remounted history stay still.
    for selector in (
        ".task-flow-group-header {\n  animation: loom-task-capsule-land",
        ".task-flow-group-icon {\n  animation: loom-task-icon-spring",
        ".task-flow-row-wrap {\n  contain: layout style;",
        ".task-flow-row {\n  animation: loom-task-capsule-land",
        ".task-flow-status-dot {\n  animation: loom-task-dot-settle",
    ):
        start = motion.index(selector)
        line_start = motion.rindex("\n", 0, start) + 1
        assert motion[line_start:start].startswith(".turn-process.is-live .task-flow-group.is-running ")


def test_status_and_title_changes_cross_fade() -> None:
    transcript = read(TRANSCRIPT)
    motion = read(MOTION)

    assert transcript.count('<span className="task-flow-verb" key={verbKey}>') == 4
    assert 'const verbKey = active ? "active" : "rested";' in transcript
    assert '<span className="task-flow-group-title" key={title}>{title}</span>' in transcript

    verb = keyframes(motion, "loom-task-verb-in")
    assert "opacity" in verb and not TRANSFORM_PROPERTY.search(verb)

    tick = keyframes(motion, "loom-task-dot-settle")
    ring = keyframes(motion, "loom-task-dot-ring")
    assert "scale:" in tick and "transform:" not in tick
    assert "opacity" in ring and "scale:" in ring
    assert ".task-flow-status:is(.completed, .changed, .failed, .denied)::after" in motion


def test_beacon_lands_on_the_first_frame_of_its_pulse() -> None:
    motion = read(MOTION)

    beacon_in = keyframes(motion, "loom-task-beacon-in")
    pulse = keyframes(motion, "loom-live-beacon")
    assert "to { opacity: .58; scale: .91; }" in beacon_in
    assert "0%, 100% { opacity: .58; transform: scale(.91); }" in pulse
    assert "loom-live-beacon 1.9s ease-in-out 660ms infinite" in motion
    assert "loom-task-beacon-in 360ms var(--loom-spring-pop) 300ms backwards" in motion


def test_dead_or_duplicate_task_motion_is_gone() -> None:
    generation = read(GENERATION)
    theme = read(THEME)

    # task-flow-runtime-performance.css pins `.turn-process.is-live .task-flow`
    # to `animation: none !important`, so a group-level entrance can never run.
    assert "loom-generation-task-anchor-in" not in generation
    assert ".turn-process.is-live .task-flow-group {" not in generation
    # The light theme used to force live rows transparent after the capsule
    # fill was defined, and to breathe the anchor icon against its beacon.
    assert 'html[data-loom-theme="light"] .task-flow-row.is-active {' not in theme
    assert "task-flow-group-breathe-light" not in theme
    assert "task-flow-shimmer-light" not in theme


def test_history_never_replays_settle_entrances() -> None:
    generation = read(GENERATION)
    motion = read(MOTION)

    assert ".turn-block.is-settling .turn-final-answer .assistant-message-meta {" in generation
    assert ".turn-block.is-settling > .turn-artifacts {" in generation
    assert "\n.turn-final-answer .assistant-message-meta {" not in generation
    assert ".turn-final-answer .decision-prompt-card" not in generation
    assert "turn-artifacts-preview" not in generation

    # Assistant entries leave their entrance to the surfaces inside them.
    assert ".turn-process.is-live .entry-assistant_message {\n  animation: none;\n}" in motion
    live_list = motion[motion.index("/* Assistant entries are deliberately absent"):motion.index("loom-live-message-in 280ms")]
    assert ".entry-assistant_message" not in live_list.split("*/", 1)[1]


def test_thinking_capsules_hand_off_instead_of_blinking() -> None:
    transcript = read(TRANSCRIPT)
    thinking = read(THINKING)
    generation = read(GENERATION)

    assert "const PendingThinkingContext = createContext<PendingThinkingHandle | null>(null);" in transcript
    assert "const [handoff] = useState(() => pendingThinkingOnScreen(pendingThinking));" in transcript
    assert '${handoff ? "is-handoff" : ""}' in transcript
    # Only a capsule the user has actually seen hands off. The streaming
    # runtime starts an empty assistant item one presentation frame before its
    # first delta; the pending capsule mounted (or un-collapsed) for that frame
    # must not swallow the reasoning capsule's birth.
    on_screen = transcript[transcript.index("function pendingThinkingOnScreen("):transcript.index("function LiveReasoning(")]
    assert "if (!element?.isConnected) return false;" in on_screen
    assert "performance.now() - pending!.mountedAt < PENDING_THINKING_SEEN_MS" in on_screen
    assert "element.getBoundingClientRect().height >= 16 && Number(getComputedStyle(element).opacity) > 0.5" in on_screen
    assert "const PENDING_THINKING_SEEN_MS = 240;" in transcript

    assert ".turn-process.is-live .live-reasoning.is-handoff .live-reasoning-trigger {" in thinking
    assert ".turn-process.is-live .live-reasoning:not(.is-handoff) .live-reasoning-trigger {" in thinking
    assert ".turn-block > .inline-thinking {\n  --capsule-bead-start: 0px;" in thinking
    assert "loom-generation-reasoning-in" not in generation

    answer = keyframes(generation, "loom-generation-answer-in")
    assert "opacity: .25;" in answer and "opacity: 0;" not in answer


def test_stickers_pop_once_in_the_live_process() -> None:
    generation = read(GENERATION)

    # Not gated on .is-streaming: the render that reveals an end-of-message
    # sticker is the same one that drops that class.
    assert (
        ".turn-process.is-live .markdown-body img.assistant-inline-sticker {\n"
        "  transform-origin: 50% 70%;\n"
        "  animation: loom-sticker-pop 220ms var(--loom-spring-soft) backwards;"
    ) in generation
    assert ".is-streaming img.assistant-inline-sticker" not in generation
    pop = keyframes(generation, "loom-sticker-pop")
    assert "opacity: 0;" not in pop, "Stickers must not disappear when remounted"
    # The sticker's own `transform` is its baseline offset; never replace it.
    assert "transform:" not in pop and "scale:" in pop and "rotate:" in pop

    reduced = generation[generation.index("@media (prefers-reduced-motion: reduce)"):]
    sticker_rule = reduced[reduced.index(".turn-process.is-live .markdown-body img.assistant-inline-sticker {"):]
    assert sticker_rule[:sticker_rule.index("}")].count("!important") == 1
    assert "transform: none" not in sticker_rule[:sticker_rule.index("}")]


def test_reduced_motion_outranks_every_capsule_birth() -> None:
    motion = read(MOTION)
    media = motion[motion.index("@media (prefers-reduced-motion: reduce)"):]
    setting = motion[motion.index(':root[data-loom-reduced-motion="true"]'):]

    for selector in (
        ".turn-process.is-live .task-flow-group.is-running .task-flow-group-header",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-group-icon",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-row-wrap",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-row",
        ".turn-process.is-live .task-flow-group-title",
        ".turn-process.is-live .task-flow-status::after",
        ".turn-process.is-live .task-flow-status-dot",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-group-header::before",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-row::before",
        ".turn-process.is-live .task-flow-group.is-running .task-flow-chevron",
        ".task-flow-group.is-unfolding .task-flow-row",
        ".task-flow-group.is-unfolding .task-flow-row::before",
        ".task-flow-group.is-unfolding .task-flow-row-icon",
    ):
        assert selector + "," in media
        assert ':root[data-loom-reduced-motion="true"] ' + selector + "," in setting


def test_reasoning_capsule_stays_inside_folding_clip() -> None:
    thinking = read(THINKING)
    layout = next(body for selector, body in rules(thinking)
                  if selector == ".live-reasoning" and "display: grid" in body)
    assert "margin-top: 0;" in layout
    assert not re.search(r"margin-top:\s*-", layout)


def test_capsule_inflate_is_a_shallow_clip_that_never_hides_text() -> None:
    motion = read(MOTION)

    for name, horizontal in (("loom-capsule-swell", "6%"), ("loom-capsule-swell-header", "8px")):
        frames = keyframes(motion, name)
        # A clip, never a transform or a fade: text-bearing surfaces stay native.
        assert "clip-path" in frames
        assert not TRANSFORM_PROPERTY.search(frames) and "opacity" not in frames
        # The first frame is only a little smaller than the capsule, so every
        # command is readable from frame 0 (the bead-sized clip that once hid
        # commands is reserved for thinking capsules).
        assert f"clip-path: inset(4px {horizontal} 4px {horizontal} round 999px)" in frames
        # It opens past the border box so the capsule's own shadow is revealed
        # before the clip is released.
        assert "inset(-26px -26px -26px -26px round 999px)" in frames

    row_rules = [body for selector, body in rules(motion)
                 if selector == ".turn-process.is-live .task-flow-group.is-running .task-flow-row"]
    assert len(row_rules) == 1
    assert "loom-task-capsule-land" in row_rules[0] and "loom-capsule-swell" in row_rules[0]
    assert "loom-capsule-unroll" not in row_rules[0]


def test_capsule_light_is_paint_only_and_rides_the_clip() -> None:
    motion = read(MOTION)
    swell = keyframes(motion, "loom-capsule-swell")

    for name in ("loom-capsule-glow", "loom-capsule-confirm"):
        frames = keyframes(motion, name)
        # The light tracks the clip edge (inset) and paints an inner shadow.
        assert "inset:" in frames and "box-shadow" in frames
        # Paint properties only: no compositor layer is ever created over text.
        assert not TRANSFORM_PROPERTY.search(frames)
        assert "opacity" not in frames and "filter" not in frames
        # Same stops as the clip it rides, so the glow edge hugs the capsule.
        for stop in ("0% {", "58% {", "100% {"):
            assert stop in frames and stop in swell


def test_status_flip_restarts_the_light_in_the_outcome_colour() -> None:
    motion = read(MOTION)

    # A running row ignites in the accent colour; a settling row confirms in its
    # outcome colour. The animation *name* differs, and that is what restarts it.
    assert (".turn-process.is-live .task-flow-group.is-running .task-flow-row.is-active::before {\n"
            "  animation: loom-capsule-glow") in motion
    assert (".turn-process.is-live .task-flow-group.is-running .task-flow-row.is-resting::before {\n"
            "  animation: loom-capsule-confirm") in motion
    assert "--capsule-glow: var(--semantic-success-strong" in motion
    assert "--capsule-glow: var(--semantic-danger-strong" in motion
    assert ".task-flow-row.is-resting:has(.task-flow-status:is(.failed, .denied))" in motion
    # Outcome colours belong to the live anchor; opening a group later is not a
    # verdict, so those rows keep the accent light.
    assert "\n.task-flow-group .task-flow-row.is-resting {" not in motion


def test_unfold_is_user_initiated_and_transient() -> None:
    transcript = read(TRANSCRIPT)
    motion = read(MOTION)

    assert "const [unfolding, setUnfolding] = useState(false);" in transcript
    assert "setUnfolding(!open);" in transcript and "setOpen(!open);" in transcript
    assert '${unfolding ? "is-unfolding" : ""}' in transcript
    assert "window.setTimeout(() => setUnfolding(false), 900)" in transcript

    for selector in (
        ".task-flow-group.is-unfolding .task-flow-row {",
        ".task-flow-group.is-unfolding .task-flow-row::before {",
        ".task-flow-group.is-unfolding .task-flow-row-icon {",
    ):
        assert selector in motion
    # The cascade hangs off the transient class only: an open group that is
    # merely mounted (history) never plays it.
    assert ".task-flow-group.is-open .task-flow-row" not in motion
    assert "--fold-lead: 230ms" in motion


def test_pointer_lens_writes_two_variables_and_respects_reduced_motion() -> None:
    lens = read(SRC / "taskCapsuleLens.ts")
    motion = read(MOTION)

    assert 'setProperty("--lens-x"' in lens and 'setProperty("--lens-y"' in lens
    assert "requestAnimationFrame" in lens
    assert 'document.documentElement.dataset.loomReducedMotion === "true"' in lens
    # A module, so its top-level state cannot collide with starterCardPointerGlow.ts.
    assert "export {};" in lens
    assert 'import "../taskCapsuleLens";' in read(TRANSCRIPT)

    start = motion.index(".task-flow-group .task-flow-row.is-expandable::after,")
    rule = motion[start:motion.index("}", start)]
    assert "var(--lens-x" in rule and "var(--lens-y" in rule
    assert "opacity: 0;" in rule and "transition: opacity" in rule
    # The light is a gradient on its own pseudo-element: nothing moves.
    assert not TRANSFORM_PROPERTY.search(rule) and "filter" not in rule

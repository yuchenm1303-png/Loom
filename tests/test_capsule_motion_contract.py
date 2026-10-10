"""Contracts for the live motion language of the transcript.

The thinking capsule (standalone or a message's reasoning header) is born
from its bead and hands off in place. The work log (weave.css, WeaveFlow.tsx)
is a thread of steps: a step is born once, when it first appears in a live
turn, and a step that finishes while the user watches confirms once; neither is
ever tied to a class that can toggle later (a stage regaining its running state
used to replay every row's birth). These tests pin the parts that are easy to
regress silently: text-bearing surfaces never animate transforms, one-shot
motion hangs off moments rather than state classes, one curve carries every
height motion, history never replays motion, and reduced motion outranks all of
it.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
TRANSCRIPT = COMPONENTS / "Transcript.tsx"
WEAVE_TSX = COMPONENTS / "WeaveFlow.tsx"
CONTEXTS = COMPONENTS / "transcriptContexts.ts"
WEAVE = COMPONENTS / "weave.css"
MOTION = COMPONENTS / "conversation-motion.css"
GENERATION = COMPONENTS / "generation-motion.css"
THINKING = COMPONENTS / "inline-thinking.css"
TURN_FLOW = COMPONENTS / "turn-flow.css"
THEME = SRC / "theme.css"

TRANSFORM_PROPERTY = re.compile(r"(?<![\w-])(transform|translate|scale|rotate)\s*:")

# Surfaces that paint log or thinking copy. Their entrances may clip, grow or
# fade, but never move or scale: Windows/HiDPI renders softened glyphs otherwise.
TEXT_BEARING = {
    "wv-step", "wv-step-inner", "wv-row", "wv-main", "wv-verb", "wv-target", "wv-meta", "wv-tag", "wv-line",
    "wv-line-text", "wv-line-failed", "wv-sub", "wv-sub-text", "wv-note", "wv-sheet", "wv-earlier", "wv-time",
    "inline-thinking", "thinking-shimmer", "live-reasoning-trigger", "live-reasoning-label", "live-reasoning-presence",
}

# Keyframes that play once per moment. Binding them to a state class that can
# toggle would replay them.
ONE_SHOT = ("wv-grow", "wv-rail-draw", "wv-glyph-in", "wv-bead-in", "wv-ring", "loom-task-icon-spring")

# What a one-shot may hang off: a moment React sets once, or an element that is
# itself born when its text changes.
MOMENTS = ('[data-born="live"]', "[data-settled]", '[data-animate="true"]', ".wv-earlier", ".wv-tag", ".wv-sub-text",
           ".wv-line-text", ".live-reasoning", ".inline-thinking")


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


def selectors(selector_list: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in selector_list:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def subject(selector: str) -> str:
    """The last compound of a selector, ignoring spaces inside :is()/[...]."""
    depth = 0
    cut = 0
    for index, char in enumerate(selector):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == " " and depth == 0:
            cut = index + 1
    return selector[cut:]


def subject_classes(selector: str) -> set[str]:
    return set(re.findall(r"\.([\w-]+)", subject(selector)))


def animation_names(body: str) -> list[str]:
    names: list[str] = []
    for declaration in re.findall(r"animation\s*:([^;]+)", body):
        # Keyframe names only; `var(--loom-...)` easing tokens are not animations.
        names.extend(re.findall(r"(?<![\w-])(?:loom-[\w-]+|wv-[\w-]+|thinking-text-shimmer)", declaration))
    return names


def test_text_bearing_surfaces_never_animate_transforms() -> None:
    weave = read(WEAVE)
    thinking = read(THINKING)
    checked: set[str] = set()

    for source in (weave, thinking):
        for selector_list, body in rules(source):
            for selector in selectors(selector_list):
                if "::" in subject(selector) or not (subject_classes(selector) & TEXT_BEARING):
                    continue
                for name in animation_names(body):
                    frames = keyframes(weave if f"@keyframes {name} {{" in weave else thinking, name)
                    assert not TRANSFORM_PROPERTY.search(frames), (selector, name)
                    checked.add(name)

    # The walk must actually reach the births, not pass vacuously.
    assert {"wv-grow", "wv-fade-in", "thinking-text-shimmer", "loom-thinking-label-in",
            "loom-thinking-header-retire"} <= checked


def test_marks_without_text_carry_the_springs() -> None:
    weave = read(WEAVE)

    # Glyphs, beads and halos carry no copy, so they may scale and tilt.
    for name in ("wv-glyph-in", "wv-bead-in", "wv-ring", "wv-halo", "wv-rail-draw"):
        assert name in keyframes(weave, name)
    for selector_list, body in rules(weave):
        for name in animation_names(body):
            if name in ("wv-glyph-in", "wv-bead-in", "wv-ring", "wv-halo", "wv-rail-draw"):
                for selector in selectors(selector_list):
                    assert not (subject_classes(selector) & TEXT_BEARING) or "::" in subject(selector), (selector, name)


def test_one_shot_motion_hangs_off_moments_not_state_classes() -> None:
    weave = read(WEAVE)
    thinking = read(THINKING)
    transcript = read(TRANSCRIPT)
    flow = read(WEAVE_TSX)
    contexts = read(CONTEXTS)

    for source in (weave, thinking):
        for selector_list, body in rules(source):
            names = set(animation_names(body)) & set(ONE_SHOT)
            if not names:
                continue
            for selector in selectors(selector_list):
                # Births and settles key on markers React sets once per moment.
                assert ".is-running" not in selector and ".is-live" not in selector.replace(".turn-process.is-live", ""), (selector, names)
                assert any(marker in selector for marker in MOMENTS), selector

    # The markers themselves: a row is born once per renderer session, only
    # inside the live sequence of an active turn.
    assert "const seenActivity = new Set<string>();" in contexts
    assert "const [born] = useState(() => live && !seenActivity.has(key));" in contexts
    assert "seenActivity.add(key);" in contexts
    assert 'data-born={born ? "live" : undefined}' in flow
    assert flow.count('data-born={born ? "live" : undefined}') == 1
    assert "<LiveSequenceContext.Provider value={active}>" in transcript
    # Settling is observed on the row itself, then cleared.
    assert "data-settled={settled ?? undefined}" in flow
    assert "!isActiveActivityStatus(previous) || isActiveActivityStatus(status)" in flow
    assert "window.setTimeout(() => setSettled(null), 760)" in flow


def test_beads_pop_on_sampled_springs() -> None:
    weave = read(WEAVE)
    motion = read(MOTION)

    pop = re.search(r"--loom-spring-pop: linear\(([^)]*)\);", motion)
    soft = re.search(r"--loom-spring-soft: linear\(([^)]*)\);", motion)
    log = re.search(r"--wv-spring: linear\(([^)]*)\);", weave)
    assert pop and soft and log
    for curve, overshoot in ((pop, 1.05), (soft, 1.02), (log, 1.05)):
        points = [float(value) for value in curve.group(1).split(",")]
        assert points[0] == 0 and points[-1] == 1
        assert max(points) > overshoot

    for name in ("wv-glyph-in", "wv-bead-in"):
        spring = keyframes(weave, name)
        # Individual transform properties never clobber an element's own transform.
        assert "transform:" not in spring and "scale:" in spring
    assert "rotate:" in keyframes(weave, "wv-glyph-in")
    assert "wv-glyph-in 460ms var(--wv-spring) 60ms backwards" in weave
    assert "wv-bead-in 360ms var(--wv-spring)" in weave
    spring = keyframes(motion, "loom-task-icon-spring")
    assert "transform:" not in spring and "scale:" in spring and "rotate:" in spring
    assert "loom-task-icon-spring 560ms var(--loom-spring-pop) var(--thinking-lead, 0ms) backwards" in read(THINKING)


def test_a_step_never_changes_its_words_and_state_changes_fade() -> None:
    flow = read(WEAVE_TSX)
    weave = read(WEAVE)

    # The verb has no tense and no key: it is the same element from the first frame to the last, and the
    # step's state is its node. Words that do change (an outcome tag, a line's counts, a printed line) are
    # keyed on their text, so each change is a birth that fades.
    assert '<span className="wv-verb" title={description.verb}>{description.label}</span>' in flow
    assert "key={description.verb}" not in flow and "key={description.label}" not in flow
    assert '<span className="wv-tag" key={status}>' in flow
    assert '<span className="wv-line-text" key={text}>{text}</span>' in flow
    assert '<span className="wv-sub-text" key={subShown.text}>' in flow

    fade = keyframes(weave, "wv-fade-in")
    assert "opacity" in fade and not TRANSFORM_PROPERTY.search(fade)
    ring = keyframes(weave, "wv-ring")
    assert "opacity" in ring and "scale:" in ring and "transform:" not in ring
    assert ".wv-step[data-settled] .wv-node::after {" in weave
    assert '.wv-step[data-settled="done"] { --wv-settle: var(--wv-ok); }' in weave
    assert '.wv-step[data-settled="failed"] { --wv-settle: var(--wv-danger); }' in weave


def test_every_height_motion_shares_one_curve_and_one_length() -> None:
    weave = read(WEAVE)

    assert "--wv-ease: cubic-bezier(.22, .74, .18, 1);" in weave
    assert "--wv-fold: 300ms;" in weave
    # Anything that grows while something else shrinks must sum to a monotonic change, which only holds when
    # both ride the same curve for the same time.
    heights = [(selector_list, body) for selector_list, body in rules(weave) if "grid-template-rows" in body
               and ("transition" in body or "animation" in body)]
    assert heights
    for selector_list, body in heights:
        for declaration in re.findall(r"transition\s*:([^;]+)", body):
            for part in declaration.split(","):
                if "grid-template-rows" in part:
                    assert "var(--wv-fold) var(--wv-ease)" in part, (selector_list, part)
    assert "animation: wv-grow var(--wv-fold) var(--wv-ease) backwards;" in weave
    # A finished animation must never pin a value over the transition that has to run later.
    assert " both" not in re.sub(r"/\*.*?\*/", "", weave, flags=re.S).replace("animation-fill-mode: both", "")
    assert "to {" not in keyframes(weave, "wv-grow")


def test_retired_capsule_layers_and_the_old_log_stack_are_gone() -> None:
    motion = read(MOTION)
    generation = read(GENERATION)
    theme = read(THEME)
    transcript = read(TRANSCRIPT)

    # The inflate clip, inner glow and outcome ring painted outlines around
    # flat log rows; the between-steps label and sheen flickered in short gaps.
    for retired in ("loom-capsule-swell", "loom-capsule-glow", "loom-capsule-confirm", "loom-capsule-unroll",
                    "loom-task-capsule-land", "loom-task-between-sheen", "loom-task-between-dot",
                    "task-flow-between-label", "task-flow-between-sheen"):
        assert retired not in motion and retired not in transcript, retired
    assert "继续处理中" not in transcript
    assert "loom-generation-task-anchor-in" not in generation
    assert ".turn-process.is-live .task-flow-group {" not in generation
    assert 'html[data-loom-theme="light"] .task-flow-row.is-active {' not in theme
    assert "task-flow-group-breathe-light" not in theme
    assert "task-flow-shimmer-light" not in theme

    # The log used to be restyled by ten stylesheets that overrode each other (some with !important), which is
    # where its competing durations and curves came from. Its classes now have one owner, weave.css.
    for path in list(SRC.rglob("*.css")) + list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        assert "task-flow-" not in read(path), path.name


def test_history_never_replays_settle_entrances() -> None:
    generation = read(GENERATION)
    motion = read(MOTION)

    flow = read(TURN_FLOW)
    transcript = read(TRANSCRIPT)
    # The answer's toolbar and the changed-files card arrive with the process
    # fold, scoped to the settle phases only a live -> complete turn passes
    # through. During the hold they take no space, so completion does not push
    # the conversation down and pull it back up a moment later.
    assert "is-settling is-settle-${settle}" in transcript
    assert ".turn-block.is-settle-hold .turn-final-answer .assistant-message-meta {" in flow
    assert ".turn-block.is-settle-fold .turn-final-answer .assistant-message-meta {" in flow
    assert ".turn-block.is-settle-hold > .turn-artifacts-slot {" in flow
    assert ".turn-block.is-settle-fold > .turn-artifacts-slot {" in flow
    assert '<div className="turn-artifacts-slot">' in transcript
    assert ".turn-block.is-settling > .turn-artifacts {" not in generation
    assert "\n.turn-final-answer .assistant-message-meta {" not in generation
    assert ".turn-final-answer .decision-prompt-card" not in generation
    assert "turn-artifacts-preview" not in generation
    # The final answer is already on screen when it leaves the live process: it
    # moves without an entrance of its own.
    assert "loom-generation-final-handoff" not in generation
    assert ".turn-final-answer {\n  animation: none !important;\n}" in motion

    # Assistant entries leave their entrance to the surfaces inside them.
    assert ".turn-process.is-live .entry-assistant_message {\n  animation: none;\n}" in motion
    assert ".has-lifecycle-motion .assistant-message > .markdown-body {" in generation


def test_thinking_capsules_hand_off_instead_of_blinking() -> None:
    transcript = read(TRANSCRIPT)
    thinking = read(THINKING)
    generation = read(GENERATION)

    assert "const ThinkingHandleContext = createContext<ThinkingHandle | null>(null);" in transcript
    assert "const [handoff] = useState(() => thinking && thinkingOnScreen(handle));" in transcript
    assert '${handoff ? "is-handoff" : ""}' in transcript
    # Only a capsule the user has actually seen hands off: not one mounted for
    # a single frame between an empty streaming item and its first delta.
    on_screen = transcript[transcript.index("function thinkingOnScreen("):transcript.index("function useThinkingRegistration(")]
    assert "if (!element?.isConnected) return false;" in on_screen
    assert "performance.now() - handle!.mountedAt < THINKING_SEEN_MS" in on_screen
    assert "element.getBoundingClientRect().height >= 16 && Number(getComputedStyle(element).opacity) > 0.5" in on_screen
    # A message that starts writing while the capsule is visible continues it
    # and folds it away above the first line.
    assert "useState(() => live && !showReasoning && thinkingOnScreen(handle))" in transcript
    assert ".live-reasoning-presence.is-inherited[data-motion-phase=\"exiting\"]" in thinking
    # The capsule leaves without an exit when a message takes its place.
    assert "!(capsulePresence.phase === \"exiting\" && handedOffRef.current)" in transcript

    assert ".turn-process.is-live .live-reasoning.is-thinking:not(.is-handoff) .live-reasoning-bead .tg" in thinking
    assert "loom-generation-reasoning-in" not in generation

    answer = keyframes(generation, "loom-generation-answer-in")
    assert "opacity: .25;" in answer and "opacity: 0;" not in answer
    assert not TRANSFORM_PROPERTY.search(answer)


def test_quiet_notes_are_hidden_whether_or_not_their_words_have_arrived() -> None:
    transcript = read(TRANSCRIPT)

    # A step that goes straight to a tool call writes no words; its still-streaming item must not draw a
    # "thinking" header beside the row that is already running (a large file streams for many seconds).
    assert "isLeadNote(block.item) && !notes.show ? null" in transcript
    assert 'String(block.item.text ?? "").trim() ? null' not in transcript


def test_thinking_header_morphs_into_the_disclosure_in_place() -> None:
    transcript = read(TRANSCRIPT)
    thinking = read(THINKING)

    # One element through the change: the bead folds away and the two labels
    # cross-fade in one grid cell, instead of swapping two surfaces.
    assert 'className="live-reasoning-label is-live thinking-shimmer"' in transcript
    assert 'className="live-reasoning-label is-rest"' in transcript
    assert ".live-reasoning.is-done .live-reasoning-bead {\n  width: 0;" in thinking
    assert ".live-reasoning-label {\n  grid-area: 1 / 1;" in thinking
    assert "transition: opacity 220ms ease;" in thinking


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


def test_reduced_motion_outranks_every_birth_and_settle() -> None:
    weave = read(WEAVE)
    media_start = weave.index("@media (prefers-reduced-motion: reduce) {")
    attribute_start = weave.index(':root[data-loom-reduced-motion="true"] :is(')
    media = weave[media_start:attribute_start]
    setting = weave[attribute_start:weave.index("@media (max-width: 760px)")]
    main = weave[:media_start]

    # Every element the log animates or transitions is named in both lists.
    moving: set[str] = set()
    for selector_list, body in rules(main):
        if not re.search(r"(animation|transition)\s*:", body):
            continue
        for selector in selectors(selector_list):
            moving |= subject_classes(selector) & {name for name in re.findall(r"\.([\w-]+)", main) if name.startswith("wv-")}
    # Containers whose only motion is their own state; every other class must be listed.
    assert {"wv-fold", "wv-sub-fold", "wv-detail", "wv-step", "wv-earlier", "wv-row", "wv-glyph", "wv-halo", "wv-verb",
            "wv-tag", "wv-chevron", "wv-line", "wv-line-text", "wv-sub-text", "wv-hint-glow", "wv-note"} <= moving
    for name in sorted(moving):
        assert f".{name}" in media, name
        assert f".{name}" in setting, name
    for block in (media, setting):
        assert "animation: none !important;" in block and "transition: none !important;" in block
        assert ")::before" in block and ")::after" in block


def test_reasoning_header_stays_inside_folding_clip() -> None:
    thinking = read(THINKING)
    layout = next(body for selector, body in rules(thinking)
                  if selector == ".live-reasoning" and "display: grid" in body)
    assert "margin-top: 0;" in layout
    assert not re.search(r"margin-top:\s*-", layout)


def test_a_folded_stage_opens_by_the_readers_click_and_keeps_the_choice() -> None:
    flow = read(WEAVE_TSX)
    weave = read(WEAVE)

    # A click opens or closes the stage; the choice overrides the automatic state until the stage runs again.
    assert "const [chosen, setChosen] = useState<boolean | null>(null);" in flow
    assert "setChosen(!open);" in flow and "setOpening(!open);" in flow
    assert 'data-opening={opening ? "true" : undefined}' in flow
    assert '.wv-stage[data-opening="true"] .wv-step .wv-row {' in weave
    assert "if (keepOpen && !wasKeepingOpen.current) setChosen(null);" in flow
    # Folded, closed and unmounted rows are one motion (Fold), and a fold that mounts open (history) plays nothing.
    assert "const settledOnMount = useRef(!animateIn && open);" in flow
    assert "if (!open) settledOnMount.current = false;" in flow
    assert ".wv-fold[data-open=\"true\"]," in weave


def test_pointer_lens_writes_two_variables_and_respects_reduced_motion() -> None:
    lens = read(SRC / "taskCapsuleLens.ts")
    weave = read(WEAVE)

    assert 'setProperty("--lens-x"' in lens and 'setProperty("--lens-y"' in lens
    assert "requestAnimationFrame" in lens
    assert 'document.documentElement.dataset.loomReducedMotion === "true"' in lens
    assert 'const CAPSULE_SELECTOR = ".wv-row.is-expandable, .wv-line";' in lens
    # A module, so its top-level state cannot collide with starterCardPointerGlow.ts.
    assert "export {};" in lens
    assert 'import "../taskCapsuleLens";' in read(TRANSCRIPT)

    start = weave.index(".wv-row.is-expandable::after,\n.wv-line::after {")
    rule = weave[start:weave.index("}", start)]
    assert "var(--lens-x" in rule and "var(--lens-y" in rule
    assert "opacity: 0;" in rule and "transition: opacity" in rule
    # The light is a gradient on its own pseudo-element: nothing moves.
    assert not TRANSFORM_PROPERTY.search(rule) and "filter" not in rule

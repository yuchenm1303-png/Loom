"""Contracts for the live motion language of the transcript.

The thinking capsule (standalone or a message's reasoning header) is born
from its bead and hands off in place. Task groups and rows are born once, when
they first appear in a live turn, and a row that finishes while the user
watches confirms once; neither is ever tied to a class that can toggle later
(a group regaining its running state used to replay every row's birth). These
tests pin the parts that are easy to regress silently: text-bearing surfaces
never animate transforms, one-shot motion hangs off moments rather than state
classes, history never replays motion, and reduced motion outranks all of it.
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
TURN_FLOW = COMPONENTS / "turn-flow.css"
THEME = SRC / "theme.css"

TRANSFORM_PROPERTY = re.compile(r"(?<![\w-])(transform|translate|scale|rotate)\s*:")

# Surfaces that paint task/thinking copy. Their entrances may clip or fade,
# but never move or scale: Windows/HiDPI renders softened glyphs otherwise.
TEXT_BEARING_SUBJECTS = (
    ".task-flow-group-header",
    ".task-flow-group-title",
    ".task-flow-verb",
    ".task-flow-primary",
    ".task-flow-diffstat",
    ".task-flow-row-wrap",
    ".task-flow-row",
    ".task-flow-row-main",
    ".inline-thinking",
    ".thinking-shimmer",
    ".live-reasoning-trigger",
    ".live-reasoning-label",
    ".live-reasoning-presence",
)

# Keyframes that play once per moment. Binding them to a state class that can
# toggle would replay them.
ONE_SHOT = (
    "loom-task-icon-spring",
    "loom-task-copy-in",
    "loom-task-verb-in",
    "loom-task-status-in",
    "loom-task-dot-settle",
    "loom-task-dot-ring",
    "loom-task-chevron-in",
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
        names.extend(re.findall(r"(?<![\w-])(?:loom-[\w-]+|thinking-text-shimmer)", declaration))
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
                    last == name or last.startswith(name + ".") or last.startswith(name + ":") or last.startswith(name + "[")
                    for name in TEXT_BEARING_SUBJECTS
                ):
                    continue
                for name in animation_names(body):
                    frames = keyframes(motion if f"@keyframes {name} {{" in motion else thinking, name)
                    assert not TRANSFORM_PROPERTY.search(frames), (selector.strip(), name)
                    checked.add(name)

    # The walk must actually reach the births, not pass vacuously.
    assert {"loom-task-copy-in", "loom-task-verb-in", "thinking-text-shimmer", "loom-thinking-label-in",
            "loom-thinking-header-retire"} <= checked


def test_one_shot_motion_hangs_off_moments_not_state_classes() -> None:
    motion = read(MOTION)
    transcript = read(TRANSCRIPT)

    for selector_list, body in rules(motion):
        names = set(animation_names(body)) & set(ONE_SHOT)
        if not names:
            continue
        for selector in selector_list.split(","):
            # Births and settles key on markers React sets once per moment.
            assert ".is-running" not in selector and ".is-live" not in selector, (selector.strip(), names)
            assert any(marker in selector for marker in ('[data-born="live"]', "[data-settled]", ".is-unfolding")), selector.strip()

    # The markers themselves: a row/group is born once per renderer session,
    # only inside the live sequence of an active turn.
    assert "const seenActivity = new Set<string>();" in transcript
    assert "const [born] = useState(() => live && !seenActivity.has(key));" in transcript
    assert "seenActivity.add(key);" in transcript
    assert 'data-born={born ? "live" : undefined}' in transcript
    assert transcript.count('data-born={born ? "live" : undefined}') == 2
    assert "<LiveSequenceContext.Provider value={active}>" in transcript
    # Settling is observed on the row itself, then cleared.
    assert "data-settled={settled ?? undefined}" in transcript
    assert "!isActiveActivityStatus(previous) || isActiveActivityStatus(status)" in transcript
    assert "window.setTimeout(() => setSettled(null), 760)" in transcript


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
    assert "animation: loom-task-icon-spring 520ms var(--loom-spring-pop) backwards;" in motion
    assert "loom-task-icon-spring 560ms var(--loom-spring-pop) var(--thinking-lead, 0ms) backwards" in read(THINKING)


def test_status_and_title_changes_cross_fade() -> None:
    transcript = read(TRANSCRIPT)
    motion = read(MOTION)

    assert '<span className="task-flow-verb" key={description.verb}>{description.verb}</span>' in transcript
    assert '<span className="task-flow-group-title" key={title}>{title}</span>' in transcript

    verb = keyframes(motion, "loom-task-verb-in")
    assert "opacity" in verb and not TRANSFORM_PROPERTY.search(verb)

    tick = keyframes(motion, "loom-task-dot-settle")
    ring = keyframes(motion, "loom-task-dot-ring")
    assert "scale:" in tick and "transform:" not in tick
    assert "opacity" in ring and "scale:" in ring
    assert '.task-flow-row-wrap[data-settled] .task-flow-status::after' in motion
    assert "--task-dot-ring: var(--semantic-success-strong" in motion
    assert "--task-dot-ring: var(--semantic-danger-strong" in motion


def test_beacon_lands_on_the_first_frame_of_its_pulse() -> None:
    motion = read(MOTION)

    beacon_in = keyframes(motion, "loom-task-beacon-in")
    pulse = keyframes(motion, "loom-live-beacon")
    assert "to { opacity: .58; scale: .91; }" in beacon_in
    assert "0%, 100% { opacity: .58; transform: scale(.91); }" in pulse
    # Only while a step actually executes: the quiet gap between steps belongs
    # to the thinking capsule, not to a second live cue on the group.
    assert ".task-flow-group.is-running:not(.is-between-steps) .task-flow-group-icon::after {" in motion
    assert "loom-live-beacon 1.9s ease-in-out 360ms infinite" in motion


def test_retired_capsule_layers_are_gone() -> None:
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
    motion = read(MOTION)
    media = motion[motion.index("/* Reduced motion"):]
    setting = media[media.index(':root[data-loom-reduced-motion="true"]'):]

    for selector in (
        '.task-flow-group[data-born="live"] > .task-flow-group-header .task-flow-group-icon',
        '.task-flow-group[data-born="live"] .task-flow-group-title',
        '.task-flow-row-wrap[data-born="live"] > .task-flow-row .task-flow-row-icon',
        '.task-flow-row-wrap[data-born="live"] > .task-flow-row .task-flow-primary',
        '.task-flow-row-wrap[data-born="live"] .task-flow-verb',
        '.task-flow-row-wrap[data-born="live"] > .task-flow-row .task-flow-status',
        ".task-flow-row-wrap[data-settled] .task-flow-status-dot",
        ".task-flow-row-wrap[data-settled] .task-flow-status::after",
        ".task-flow-group.is-unfolding .task-flow-row",
        ".task-flow-row.is-executing .task-flow-verb",
    ):
        assert selector + "," in media[:media.index(':root[data-loom-reduced-motion="true"]')], selector
        assert ':root[data-loom-reduced-motion="true"] ' + selector + "," in setting, selector


def test_reasoning_header_stays_inside_folding_clip() -> None:
    thinking = read(THINKING)
    layout = next(body for selector, body in rules(thinking)
                  if selector == ".live-reasoning" and "display: grid" in body)
    assert "margin-top: 0;" in layout
    assert not re.search(r"margin-top:\s*-", layout)


def test_unfold_is_user_initiated_and_transient() -> None:
    transcript = read(TRANSCRIPT)
    motion = read(MOTION)

    assert "const [unfolding, setUnfolding] = useState(false);" in transcript
    # A click opens or closes the stage and starts the one-shot unfold; the choice overrides the automatic state.
    assert "setUnfolding(!open);" in transcript and "setChosen(!open);" in transcript
    assert '${unfolding ? "is-unfolding" : ""}' in transcript
    assert "window.setTimeout(() => setUnfolding(false), 900)" in transcript

    assert ".task-flow-group.is-unfolding .task-flow-row {" in motion
    # The cascade hangs off the transient class only: an open group that is
    # merely mounted (history) never plays it.
    assert ".task-flow-group.is-open .task-flow-row {" not in motion
    assert "--fold-lead: 195ms" in motion
    unfold = keyframes(motion, "loom-task-copy-in")
    assert "opacity" in unfold and not TRANSFORM_PROPERTY.search(unfold)


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

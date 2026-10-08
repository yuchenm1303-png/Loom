"""Contracts for the live light of an executing task row.

The moving sheen band (one wide gradient sweeping each running pill) was
retired with the pill material: on flat log rows it read as a loading
skeleton. An executing step now carries the same soft light as the thinking
label, painted through its verb, and its status dot breathes. These tests pin
that it stays one paint-only layer, follows the runtime state, keeps separate
dark and light palettes and stops under reduced motion.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "desktop-react" / "src" / "components"
MOTION = COMPONENTS / "conversation-motion.css"
THINKING = COMPONENTS / "inline-thinking.css"
TRANSCRIPT = COMPONENTS / "Transcript.tsx"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(source: str, start: str) -> str:
    begin = source.index(start)
    return source[begin:source.index("\n}\n", begin)]


def test_executing_verb_is_lit_by_the_thinking_light() -> None:
    motion = read(MOTION)

    rule = block(motion, ".task-flow-row.is-executing .task-flow-verb {")
    # The verb's own glyphs painted through a gradient: no overlay layer.
    assert "-webkit-background-clip: text;" in rule and "background-clip: text;" in rule
    assert "-webkit-text-fill-color: transparent;" in rule
    assert "animation: thinking-text-shimmer 2.2s" in rule
    assert "will-change" not in rule and "filter" not in rule
    # One hue: the gradient only mixes the verb's two tones.
    gradient = rule[rule.index("background-image: linear-gradient("):]
    gradient = gradient[:gradient.index(");")]
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", gradient) == []
    # The sweep itself only moves the background.
    shimmer = read(THINKING)
    sweep = shimmer[shimmer.index("@keyframes thinking-text-shimmer"):]
    sweep = sweep[:sweep.index("\n}\n")]
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", sweep))) == {"background-position"}


def test_live_light_follows_runtime_state_only() -> None:
    motion = read(MOTION)
    transcript = read(TRANSCRIPT)

    # The class is the runtime truth; nothing replays when it goes away.
    assert '${executing ? "is-executing" : ""}' in transcript
    dot = block(motion, ".task-flow-status.running .task-flow-status-dot,")
    assert "animation: loom-task-dot-breathe 1.8s ease-in-out infinite;" in dot
    breathe = motion[motion.index("@keyframes loom-task-dot-breathe"):]
    breathe = breathe[:breathe.index("\n}\n")]
    assert "opacity" in breathe and "scale:" in breathe and "transform:" not in breathe


def test_live_light_has_separate_dark_and_light_palettes() -> None:
    motion = read(MOTION)

    assert "--loom-live-accent: 153, 145, 226;" in motion
    assert "--loom-live-accent-strong: 181, 174, 244;" in motion
    assert "--loom-live-accent: 96, 84, 194;" in motion
    assert "--loom-live-accent-strong: 112, 100, 210;" in motion

    dark = block(motion, ".task-flow-row.is-executing .task-flow-verb {")
    light = block(motion, 'html[data-loom-theme="light"] .task-flow-row.is-executing .task-flow-verb {')
    assert "--shimmer-peak: #e6e3f8;" in dark
    assert "--shimmer-peak: #4f47b0;" in light


def test_retired_sheen_band_stays_gone() -> None:
    motion = read(MOTION)

    assert "loom-task-running-sheen" not in motion
    assert ".task-flow-sheen > i" not in motion
    assert "loom-live-anchor-aura" not in motion
    # The legacy element is still rendered by Transcript for old CSS overrides;
    # it is never shown.
    assert ".task-flow-row > .task-flow-sheen {\n  display: none;\n}" in motion


def test_live_light_obeys_reduced_motion() -> None:
    motion = read(MOTION)

    reduced = motion[motion.index("/* Reduced motion"):]
    media = reduced[:reduced.index(':root[data-loom-reduced-motion="true"]')]
    setting = reduced[reduced.index(':root[data-loom-reduced-motion="true"]'):]
    for selector in (".task-flow-row.is-executing .task-flow-verb", ".task-flow-status .task-flow-status-dot"):
        assert selector + "," in media
        assert ':root[data-loom-reduced-motion="true"] ' + selector + "," in setting

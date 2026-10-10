"""Contracts for the live light of a running step.

The moving sheen band (one wide gradient sweeping each running pill) was
retired with the pill material: on flat log rows it read as a loading
skeleton. A running step now carries the same soft light as the thinking
label, painted through its verb, and its node breathes a halo. These tests pin
that it stays one paint-only layer, follows the runtime state, takes its colours
from the theme and stops under reduced motion.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
WEAVE = COMPONENTS / "weave.css"
MOTION = COMPONENTS / "conversation-motion.css"
THINKING = COMPONENTS / "inline-thinking.css"
FLOW = COMPONENTS / "WeaveFlow.tsx"
THEME = SRC / "theme.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(source: str, start: str) -> str:
    begin = source.index(start)
    return source[begin:source.index("\n}\n", begin)]


def test_running_verb_is_lit_by_the_thinking_light() -> None:
    weave = read(WEAVE)

    rule = block(weave, ".wv-row.is-executing .wv-verb {")
    # The verb's own glyphs painted through a gradient: no overlay layer.
    assert "-webkit-background-clip: text;" in rule and "background-clip: text;" in rule
    assert "-webkit-text-fill-color: transparent;" in rule
    assert "animation: thinking-text-shimmer 2.2s" in rule
    assert "will-change" not in rule and "filter" not in rule
    # One hue: the gradient only mixes the verb's two tones, taken from the theme.
    gradient = rule[rule.index("background-image: linear-gradient("):]
    gradient = gradient[:gradient.index(");")]
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", gradient) == []
    # The sweep itself only moves the background.
    shimmer = read(THINKING)
    sweep = shimmer[shimmer.index("@keyframes thinking-text-shimmer"):]
    sweep = sweep[:sweep.index("\n}\n")]
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", sweep))) == {"background-position"}


def test_live_light_follows_runtime_state_only() -> None:
    weave = read(WEAVE)
    flow = read(FLOW)

    # The class is the runtime truth; nothing replays when it goes away.
    assert '${executing ? "is-executing" : ""}' in flow
    assert 'data-tone={tone}' in flow
    halo = block(weave, '.wv-step[data-tone="running"] .wv-halo,')
    assert "animation: wv-halo 2s ease-out infinite;" in halo
    # The halo is a soft disc on a mark with no text in it: opacity and transform only.
    keyframes = weave[weave.index("@keyframes wv-halo"):]
    keyframes = keyframes[:keyframes.index("\n}\n")]
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", keyframes))) == {"opacity", "transform"}


def test_live_light_takes_its_colours_from_the_theme() -> None:
    weave = read(WEAVE)
    motion = read(MOTION)
    theme = read(THEME)

    assert "--loom-live-accent: 153, 145, 226;" in motion
    assert "--loom-live-accent-strong: 181, 174, 244;" in motion
    assert "--loom-live-accent: 96, 84, 194;" in motion
    assert "--loom-live-accent-strong: 112, 100, 210;" in motion

    # The verb's light is made of the log's own ink and accent, so dark and light themes never share a literal.
    rule = block(weave, ".wv-row.is-executing .wv-verb {")
    assert "--shimmer-peak: color-mix(in srgb, var(--wv-ink) 92%, var(--wv-accent));" in rule
    assert "--wv-ink: var(--loom-workspace-ink);" in weave
    assert "--wv-accent: rgb(var(--loom-live-accent-strong" in weave
    dark = re.search(r"--loom-workspace-ink: (#[0-9a-f]{6});", theme)
    light = re.search(r'html\[data-loom-theme="light"\] \{[^}]*--loom-workspace-ink: (#[0-9a-f]{6});', theme, flags=re.S)
    assert dark and light and dark.group(1) != light.group(1)


def test_retired_sheen_band_stays_gone() -> None:
    weave = read(WEAVE)
    motion = read(MOTION)
    flow = read(FLOW)

    for retired in ("loom-task-running-sheen", "loom-live-anchor-aura", "task-flow-sheen", "wv-sheen"):
        assert retired not in motion and retired not in weave and retired not in flow, retired
    # The legacy sheen element is no longer rendered at all.
    assert "<i />" not in flow


def test_live_light_obeys_reduced_motion() -> None:
    weave = read(WEAVE)

    media_start = weave.index("@media (prefers-reduced-motion: reduce) {")
    attribute_start = weave.index(':root[data-loom-reduced-motion="true"] :is(')
    media = weave[media_start:attribute_start]
    setting = weave[attribute_start:weave.index("@media (max-width: 760px)")]
    for selector in (".wv-halo", ".wv-verb", ".wv-hint-glow"):
        assert selector in media
        assert selector in setting

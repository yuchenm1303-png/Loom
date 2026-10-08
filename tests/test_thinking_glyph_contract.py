import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"

# Earlier marks of the thinking row, each of which once stacked on top of the next:
# the three-stylesheet span (thinking-symbol/orbit/dot), and the ringed planet with
# a thread racing round it (tg-ply, tg-orbit, the orbit paths and their clip ids).
# The planet was retired for a single bead; any survivor would draw over it.
RETIRED = re.compile(
    r"thinking-(?:symbol|orbit|dot)\b"
    r"|loom-thinking-(?:orbit|dot|ripple)"
    r"|thinking-(?:thread-drift|shuttle-pulse|symbol-breathe)"
    r"|inline-thinking-orb"
    r"|tg-(?:ply|orbit|track|far|halo|lift)\b"
)

# The label's old sweep painted white, lavender and cyan; the reasoning rail used
# the same cyan. Thinking has one hue now.
RETIRED_COLOURS = ("#7bc8ff", "#b7a8ff", "rgba(244,242,255", "rgba(91,184,246", "rgba(50,157,225", "rgba(74,168,236", "rgba(64,158,224")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def keyframes(source: str, name: str) -> str:
    start = source.index(f"@keyframes {name} " + "{")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(f"unterminated @keyframes {name}")


def test_both_thinking_states_render_the_same_glyph() -> None:
    transcript = read(COMPONENTS / "Transcript.tsx")

    assert 'import { ThinkingGlyph } from "./ThinkingGlyph";' in transcript
    # PendingThinking and the LiveReasoning trigger.
    assert transcript.count("<ThinkingGlyph />") == 2


def test_retired_glyph_layers_are_gone() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in SRC.rglob("*")
        if path.suffix in {".css", ".ts", ".tsx"} and RETIRED.search(read(path))
    ]

    assert offenders == []
    assert not (COMPONENTS / "inline-thinking-orb.css").exists()

    row = read(COMPONENTS / "inline-thinking.css")
    for colour in RETIRED_COLOURS:
        assert colour not in row, colour


def test_glyph_is_one_bead_and_a_pulse_that_own_their_classes() -> None:
    glyph = read(COMPONENTS / "ThinkingGlyph.tsx")

    assert 'import "./thinking-glyph.css";' in glyph
    assert 'aria-hidden="true"' in glyph
    # No drawing any more: two empty elements and nothing to keep in sync.
    assert glyph.count('<i className="tg-') == 2
    assert '<i className="tg-pulse" />' in glyph and '<i className="tg-bead" />' in glyph
    assert "<svg" not in glyph and "useId" not in glyph

    for path in SRC.rglob("*.css"):
        if path.name in {"thinking-glyph.css", "inline-thinking.css", "workspace-panels.css"}:
            continue
        assert ".tg" not in read(path), path.name


def test_glyph_moves_only_by_transform_and_opacity() -> None:
    css = read(COMPONENTS / "thinking-glyph.css")

    pulse = keyframes(css, "tg-pulse")
    bead = keyframes(css, "tg-bead")
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", pulse))) <= {"opacity", "transform"}
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", bead))) <= {"transform"}
    # One pulse, one breath, both once per cycle, neither a loop that never rests.
    assert css.count("infinite") == 2
    # The glow and halo are static; they are never animated.
    assert "box-shadow" not in pulse + bead


def test_label_is_lit_by_one_monochrome_band() -> None:
    row = read(COMPONENTS / "inline-thinking.css")

    shimmer = block(row, ".thinking-shimmer {\n  --shimmer-base", "\n}\n")
    # The label's own text painted through a gradient: no second copy of the
    # string (which had to be kept in sync by hand) and no overlay layer.
    assert "-webkit-background-clip: text;" in shimmer and "background-clip: text;" in shimmer
    assert "-webkit-text-fill-color: transparent;" in shimmer
    assert ".thinking-shimmer::after" not in row and "正在思考…\"" not in row.split(".thinking-shimmer {")[0]
    assert 'content: "正在' not in row
    # One hue: the gradient only mixes the label's own two tones (declared once,
    # above it), never a colour of its own.
    gradient = block(shimmer, "background-image: linear-gradient(", "\n  );")
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", gradient) == []
    assert gradient.count("var(--shimmer-peak)") >= 5 and gradient.count("var(--shimmer-base)") >= 5
    # The band is feathered: it ramps up and down in several steps, not one edge.
    assert gradient.count("color-mix(in srgb") >= 6
    # Painted, not promoted: nothing here asks for a compositor layer behind text.
    assert "will-change" not in row
    assert "contain: paint;" in shimmer
    # The sweep itself only moves the background.
    sweep = keyframes(row, "thinking-text-shimmer")
    assert set(re.findall(r"([a-z-]+)\s*:", re.sub(r"@keyframes[^{]+", "", sweep))) == {"background-position"}
    # Forced colours drop backgrounds, so the plain label must come back.
    forced = block(row, "@media (forced-colors: active) {", "\n}\n")
    assert "-webkit-text-fill-color: currentColor;" in forced and "animation: none;" in forced


def test_birth_keeps_the_sweep_running_beside_the_label_fade() -> None:
    row = re.sub(r"\s+", " ", read(COMPONENTS / "inline-thinking.css"))

    # The label's fade-in and the sweep are both `animation` on one element; the
    # more specific birth rules would otherwise replace the sweep with the fade.
    # Two births: the standalone capsule and a message's thinking header.
    assert row.count("backwards, var(--thinking-sweep);") == 2
    assert "--thinking-sweep: thinking-text-shimmer var(--thinking-cycle, 2.6s)" in row


def test_row_and_glyph_share_one_cadence() -> None:
    row = read(COMPONENTS / "inline-thinking.css")
    glyph = read(COMPONENTS / "thinking-glyph.css")

    assert "--thinking-cycle: 2.6s;" in block(row, ".inline-thinking,\n.live-reasoning {", "}")
    assert "thinking-text-shimmer var(--thinking-cycle, 2.6s)" in row
    assert "tg-pulse var(--thinking-cycle, 2.6s)" in glyph
    assert "tg-bead var(--thinking-cycle, 2.6s)" in glyph
    # The open reasoning panel's rail breathes with the same cycle.
    assert "animation: reasoning-rail-sweep var(--thinking-cycle, 2.6s)" in row


def test_glyph_and_label_hold_still_under_reduced_motion() -> None:
    glyph = read(COMPONENTS / "thinking-glyph.css")
    row = read(COMPONENTS / "inline-thinking.css")

    media = block(glyph, "@media (prefers-reduced-motion: reduce)", "\n}\n")
    assert ".tg-bead" in media and ".tg-pulse" in media and "animation: none;" in media
    setting = block(glyph, ':root[data-loom-reduced-motion="true"] .tg-bead', "}")
    assert ".tg-pulse" in setting and "animation: none;" in setting

    # The label's band parks off the text, so the label is its plain base colour.
    media = block(row, "@media (prefers-reduced-motion: reduce)", "\n}\n")
    assert ".thinking-shimmer," in media and "animation: none !important;" in media
    assert "background-position: 98% 0;" in media
    assert ':root[data-loom-reduced-motion="true"] .thinking-shimmer {\n  background-position: 98% 0;' in row


def test_unseen_glyph_does_not_keep_animating() -> None:
    row = read(COMPONENTS / "inline-thinking.css")
    panels = read(COMPONENTS / "workspace-panels.css")
    lifecycle = read(COMPONENTS / "task-lifecycle-motion.css")
    transcript = read(COMPONENTS / "Transcript.tsx")

    # The standalone capsule is unmounted (not merely collapsed) while task rows
    # or text speak for the turn, and pauses its light while it fades out.
    assert "const wantsCapsule = active && edge.quiet && pendingPresentations.size === 0;" in transcript
    exiting = block(lifecycle, '.app-shell .pending-thinking-presence[data-motion-phase="exiting"] .thinking-shimmer', "}")
    assert "animation-play-state: paused;" in exiting
    # A header that has settled into its disclosure keeps a folded, paused bead.
    settled = block(row, ".live-reasoning.is-done .live-reasoning-bead .tg-pulse,", "}")
    assert ".live-reasoning.is-done .live-reasoning-bead .tg-bead" in settled
    assert "animation-play-state: paused;" in settled
    guard = block(panels, "Unified panel-transition performance guard", "}")
    for part in (".tg-pulse", ".tg-bead", ".thinking-shimmer"):
        assert f"body.loom-panel-motion .workspace-panels {part}" in guard
    assert "animation-play-state: paused !important;" in guard

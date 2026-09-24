import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"

# The old mark was one span restyled by three stylesheets (inline-thinking.css,
# inline-thinking-orb.css and conversation-motion.css); what rendered was all of
# them stacked. Any survivor would stack on top of the new glyph again.
RETIRED = re.compile(
    r"thinking-(?:symbol|orbit|dot)\b"
    r"|loom-thinking-(?:orbit|dot|ripple)"
    r"|thinking-(?:thread-drift|shuttle-pulse|symbol-breathe)"
    r"|inline-thinking-orb"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


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


def test_glyph_owns_its_classes() -> None:
    glyph = read(COMPONENTS / "ThinkingGlyph.tsx")

    assert 'import "./thinking-glyph.css";' in glyph
    assert 'aria-hidden="true"' in glyph
    # Gradient and clip ids must be unique per mount and safe inside url(#...).
    assert 'useId().replace(/[^\\w-]/g, "")' in glyph
    # The far half of the orbit is drawn under the bead, the near half over it.
    assert glyph.index("-far)") < glyph.index("-bead)") < glyph.index("-near)")

    for path in SRC.rglob("*.css"):
        if path.name in {"thinking-glyph.css", "inline-thinking.css", "workspace-panels.css"}:
            continue
        assert ".tg" not in read(path), path.name


def test_glyph_shares_the_row_cadence() -> None:
    row = read(COMPONENTS / "inline-thinking.css")
    glyph = read(COMPONENTS / "thinking-glyph.css")

    assert "--thinking-cycle: 2.15s;" in block(row, ".inline-thinking,\n.live-reasoning {", "}")
    assert "thinking-text-shimmer var(--thinking-cycle, 2.15s)" in row
    assert "tg-orbit var(--thinking-cycle, 2.15s)" in glyph


def test_glyph_holds_still_under_reduced_motion() -> None:
    glyph = read(COMPONENTS / "thinking-glyph.css")

    media = block(glyph, "@media (prefers-reduced-motion: reduce)", "\n}\n")
    assert ".tg-ply" in media and "animation: none;" in media
    setting = block(glyph, ':root[data-loom-reduced-motion="true"] .tg-ply', "}")
    assert "animation: none;" in setting


def test_unseen_glyph_does_not_keep_animating() -> None:
    row = read(COMPONENTS / "inline-thinking.css")
    panels = read(COMPONENTS / "workspace-panels.css")

    collapsed = block(row, ".turn-block:has(.task-flow-group.is-running) > .inline-thinking .tg-ply", "}")
    assert "animation-play-state: paused;" in collapsed
    guard = block(panels, "Unified panel-transition performance guard", "}")
    assert "body.loom-panel-motion .workspace-panels .tg-ply" in guard
    assert "animation-play-state: paused !important;" in guard

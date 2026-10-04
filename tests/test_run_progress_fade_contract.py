from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PROGRESS = ROOT / "desktop-react" / "src" / "components" / "run-progress.css"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"


def test_top_run_strip_is_compact_but_full_width() -> None:
    progress = RUN_PROGRESS.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    assert "min-height: 38px;" in motion
    assert "padding: 3px 16px;" in motion
    assert "align-self: stretch;" in progress


def test_transcript_content_fades_before_reaching_top_status() -> None:
    motion = MOTION.read_text(encoding="utf-8")
    progress = RUN_PROGRESS.read_text(encoding="utf-8")

    # The dissolve follows the transcript grid row, independently of a live
    # strip's height. A fixed top offset would cover a wrapped status strip.
    assert ".conversation-stage:has(.transcript-entry)::after" in motion
    assert "grid-row: 2;" in motion
    assert "top: 0;" in motion
    assert "transparent 100%" in motion
    assert "max-width: min(48vw, 640px);" not in progress
    assert "-webkit-mask-image" not in progress


def test_transcript_edges_dissolve_into_header_and_composer() -> None:
    motion = MOTION.read_text(encoding="utf-8")

    assert ".conversation-stage:has(.transcript-entry)::before" in motion
    assert "bottom: 0;" in motion
    assert "transform: scaleY(-1);" in motion

    assert ".conversation-stage:has(.transcript-entry)::after" in motion
    assert "top: 0;" in motion
    assert "var(--bg) 0%" in motion
    assert 'html[data-loom-theme="light"] .conversation-stage,' in motion
    assert "pointer-events: none;" in motion

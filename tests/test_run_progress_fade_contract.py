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

    assert ".conversation-stage.is-running::after" in motion
    assert "top: 38px;" in motion
    assert "height: 34px;" in motion
    assert "transparent 100%" in motion
    assert "max-width: min(48vw, 640px);" not in progress
    assert "-webkit-mask-image" not in progress

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PROGRESS = ROOT / "desktop-react" / "src" / "components" / "run-progress.css"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"


def test_top_run_strip_floats_without_moving_the_conversation() -> None:
    progress = RUN_PROGRESS.read_text(encoding="utf-8")
    refinement = (RUN_PROGRESS.parent / "workspace-surface-refinement.css").read_text(encoding="utf-8")
    app = (RUN_PROGRESS.parents[1] / "App.tsx").read_text(encoding="utf-8")

    # The strip is an overlay on the transcript's top edge, aligned with the
    # conversation column; starting or ending a run never shifts the transcript.
    strip = progress[progress.index(".conversation-stage > .run-progress-frame.top {"):]
    strip = strip[:strip.index("}")]
    assert "position: absolute;" in strip and "top: 0;" in strip
    assert "right: calc(var(--task-plan-width, 0px) + var(--ws-scroll-inset, 0px));" in strip
    assert "width: min(var(--content-width, 860px), calc(100% - 2 * var(--ws-gutter, 26px)));" in progress
    # The transcript reserves the strip's height permanently.
    assert "padding: 40px 0 28px;" in refinement
    # It enters and leaves through presence, holding the outcome readable first.
    assert "useMotionPresence(Boolean(running), RUN_STRIP_EXIT_MS)" in app
    assert '.run-progress-frame.top[data-motion-phase="exiting"] {' in progress
    assert "transition: opacity 320ms cubic-bezier(.42,0,.72,.2) 560ms;" in progress


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

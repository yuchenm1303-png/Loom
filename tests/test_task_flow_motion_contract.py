from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"
SCROLL = ROOT / "desktop-react" / "src" / "components" / "TranscriptScrollController.tsx"


def test_collapsed_activity_rows_do_not_reconcile_detail_streams() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")

    assert "sameActivityRowProps" in source
    assert "if (next.open) return activityDetail(previous.item) === activityDetail(next.item);" in source
    assert "animationDelay" not in source
    assert "openRows.has(item.id)" in source


def test_task_capsule_motion_does_not_scale_the_text_row() -> None:
    source = MOTION.read_text(encoding="utf-8")
    start = source.index("@keyframes loom-task-row-enter")
    end = source.index("@keyframes loom-task-capsule-bloom", start)
    row_motion = source[start:end]

    assert "scaleX(" not in row_motion
    assert "scaleY(" not in row_motion
    assert ".turn-process.is-live .task-flow-row::after" in source
    assert "loom-task-icon-spring" in source
    assert "will-change: transform, opacity;" in source


def test_new_activity_rows_coordinate_bottom_follow_before_paint() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "latestActivityItemId" in source
    assert "snapBottomRef" in source
    assert "activityAdded && followingRef.current" in source
    assert "scheduleBottomSync(scroller, false, true)" in source

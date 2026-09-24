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


def test_task_capsule_motion_keeps_text_on_native_rasterization_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    row_start = source.index("@keyframes loom-task-row-enter")
    row_end = source.index("@keyframes loom-task-icon-spring", row_start)
    row_motion = source[row_start:row_end]
    assert "transform:" not in row_motion

    copy_start = source.index("@keyframes loom-task-copy-in")
    copy_end = source.index("@keyframes loom-task-status-in", copy_start)
    copy_motion = source[copy_start:copy_end]
    assert "transform:" not in copy_motion

    live_start = source.index(".turn-process.is-live .task-flow-row-wrap {")
    live_end = source.index(".turn-process.is-live .task-flow-list {", live_start)
    live_motion = source[live_start:live_end]
    assert "will-change:" not in live_motion
    assert " backwards" in live_motion
    assert ".task-flow-row.is-expandable:hover {\n  /* activity-flow.css used to translate" in source
    assert "transform: none;" in source
    assert ".turn-process.is-live .task-flow-row::after" not in source
    assert "loom-task-icon-spring" in source


def test_task_flow_copy_uses_whole_pixel_font_geometry() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert ".task-flow-group-title {\n  font-size: 12px;\n  line-height: 16px;" in source
    assert ".task-flow-primary.code {\n  font-size: 11px;\n  line-height: 16px;" in source
    assert ".task-flow-group .task-flow-primary.code {\n  font-size: 11px;\n  line-height: 15px;" in source


def test_new_activity_rows_coordinate_bottom_follow_before_paint() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "latestActivityItemId" in source
    assert "snapBottomRef" in source
    assert "activityAdded && followingRef.current" in source
    assert "scheduleBottomSync(scroller, false, true)" in source

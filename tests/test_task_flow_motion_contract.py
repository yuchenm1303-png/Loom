from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"
TURN_FLOW = ROOT / "desktop-react" / "src" / "components" / "turn-flow.css"
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

    live_start = source.index(".turn-process.is-live .task-flow-group.is-running .task-flow-row-wrap {")
    live_end = source.index(".turn-process.is-live .task-flow-list {", live_start)
    live_motion = source[live_start:live_end]
    assert "will-change:" not in live_motion
    assert " backwards" in live_motion
    assert " both" not in live_motion
    assert ".task-flow-row.is-expandable:hover {\n  /* activity-flow.css used to translate" in source
    assert "transform: none;" in source
    assert ".turn-process.is-live .task-flow-row::after" not in source
    assert "loom-task-icon-spring" in source


def test_task_flow_copy_uses_whole_pixel_font_geometry() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert ".task-flow-group-title {\n  font-size: 12px;\n  line-height: 16px;" in source
    assert ".task-flow-primary.code {\n  font-size: 11px;\n  line-height: 16px;" in source
    assert ".task-flow-group .task-flow-primary.code {\n  font-size: 11px;\n  line-height: 15px;" in source


def test_new_activity_rows_use_live_follow_without_hard_snap() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "latestActivityItemId" in source
    assert "activityAdded && followingRef.current" in source
    branch_start = source.index("else if (activityAdded && followingRef.current)")
    branch_end = source.index("} else if (followingRef.current)", branch_start)
    activity_branch = source[branch_start:branch_end]
    assert "scheduleBottomSync(scroller);" in activity_branch
    assert "false, true" not in activity_branch


def test_user_scroll_up_can_break_live_follow_while_streaming() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "const detachFromLiveFollow = (scroller: HTMLDivElement) => {" in source
    assert "userDetachedRef.current = true;" in source
    assert "forceBottomRef.current = false;" in source
    assert "snapBottomRef.current = false;" in source
    assert "cancelScheduledScroll();" in source

    wheel_start = source.index("const onWheel = (event: WheelEvent) => {")
    wheel_end = source.index("const onTouchStart", wheel_start)
    wheel_handler = source[wheel_start:wheel_end]
    assert "event.deltaY < 0" in wheel_handler
    assert "detachFromLiveFollow(scroller);" in wheel_handler
    assert "event.deltaY > 0" in wheel_handler
    assert "markReturnIntent();" in wheel_handler

    assert 'scroller.addEventListener("wheel", onWheel, { passive: true });' in source
    assert 'scroller.addEventListener("touchmove", onTouchMove, { passive: true });' in source


def test_programmatic_upward_scroll_never_detaches_live_follow() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    scroll_start = source.index("const onScroll = () => {")
    scroll_end = source.index("const onWheel = (event: WheelEvent) => {", scroll_start)
    scroll_handler = source[scroll_start:scroll_end]

    assert "scrollbarPointerRef.current !== null && movedUp" in scroll_handler
    assert "detachFromLiveFollow(scroller);" in scroll_handler
    assert "if (movedUp && !forceBottomRef.current)" not in scroll_handler
    assert "followingRef.current = false;" not in scroll_handler
    assert "Never infer user intent from direction alone" in scroll_handler


def test_explicit_user_input_detaches_and_explicit_return_resumes_follow() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "userDetachedRef.current = true;" in source
    assert "returnIntentUntilRef.current = performance.now() + USER_RETURN_INTENT_MS;" in source
    assert 'if (event.deltaY < 0) {' in source
    assert "detachFromLiveFollow(scroller);" in source
    assert 'else if (event.deltaY > 0) {' in source
    assert "markReturnIntent();" in source
    assert "resumeIfUserReturnedToBottom" in source
    assert "userDetachedRef.current = false;" in source


def test_running_state_is_read_from_ref_inside_long_lived_resize_observer() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "const runningRef = useRef(Boolean(running));" in source
    assert "runningRef.current = nextRunning;" in source
    assert "const liveMotion = runningRef.current || performance.now() < settleUntilRef.current;" in source


def test_completion_handoff_finishes_with_an_exact_bottom_pin() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    assert "FINAL_SETTLE_PIN_DELAY_MS = LIVE_SETTLE_WINDOW_MS + 90" in source
    assert "settleTimerRef.current = window.setTimeout" in source
    assert "if (!latestScroller || !followingRef.current || userDetachedRef.current) return;" in source
    assert "scheduleBottomSync(latestScroller, false, true);" in source


def test_detached_scroll_state_survives_streaming_content_growth() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    observer_start = source.index("new ResizeObserver(() => {")
    observer_end = source.index("observer?.observe(scroller);", observer_start)
    observer = source[observer_start:observer_end]
    assert "} else {" in observer
    assert "setJumpVisible(!isNearBottom(scroller));" in observer


def test_completed_process_fold_keeps_intrinsic_geometry_stable() -> None:
    source = TURN_FLOW.read_text(encoding="utf-8")

    # Open/closed must share the same inner geometry. Tying the margin/padding
    # to .is-open makes those dimensions disappear before the 0fr close
    # transition runs, producing the visible snap that regressed the fold.
    assert ".turn-process.is-settled .turn-process-content {" in source
    assert ".turn-process.is-settled.is-open .turn-process-content {" not in source
    assert "margin: 5px 0 5px 16px;" in source
    assert "padding: 8px 0 8px 17px;" in source
    assert "overflow-anchor: none;" in source

    closed_transition = source.index(".turn-process.is-settled .turn-process-grid {")
    open_transition = source.index(".turn-process.is-settled.is-open .turn-process-grid {")
    assert closed_transition < open_transition
    assert "opacity 170ms ease 64ms" in source[closed_transition:open_transition]


def test_jump_to_latest_reuses_owned_follow_scheduler() -> None:
    source = SCROLL.read_text(encoding="utf-8")

    jump_start = source.index("const jumpToLatest = () => {")
    jump_end = source.index("return (", jump_start)
    jump = source[jump_start:jump_end]
    assert "userDetachedRef.current = false;" in jump
    assert "scheduleBottomSync(scroller, true, reducedMotion);" in jump
    assert "scroller.scrollTo(" not in jump

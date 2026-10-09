from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"
TURN_FLOW = ROOT / "desktop-react" / "src" / "components" / "turn-flow.css"
SCROLL = ROOT / "desktop-react" / "src" / "components" / "TranscriptScrollController.tsx"


def test_collapsed_activity_rows_do_not_reconcile_detail_streams() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")

    assert "sameActivityRowProps" in source
    assert "if (next.open) return rowDetail(previous.row, previous.delta) === rowDetail(next.row, next.delta);" in source
    assert "animationDelay" not in source
    assert "openRows.has(row.key)" in source


def test_task_capsule_motion_keeps_text_on_native_rasterization_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    copy_start = source.index("@keyframes loom-task-copy-in")
    copy_end = source.index("@keyframes loom-task-status-in", copy_start)
    copy_motion = source[copy_start:copy_end]
    assert "transform:" not in copy_motion and "translate" not in copy_motion

    births_start = source.index("/* Task flow: births")
    births_end = source.index("/* Task flow: settling", births_start)
    births = source[births_start:births_end]
    assert "will-change:" not in births
    assert " backwards" in births
    assert " both" not in births
    assert ".task-flow-row.is-expandable:hover,\n.task-flow-group .task-flow-row.is-expandable:hover {\n  /* activity-flow.css used to translate" in source
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


def test_between_tool_steps_keep_one_live_handoff_surface() -> None:
    transcript = TRANSCRIPT.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    # The latest group stays the motion anchor between tool batches, so rows
    # appended later are still born live...
    assert "const activeActivityBlocks = useMemo(() => {" in transcript
    assert "const latestActivityBlockIndex = useMemo(() => {" in transcript
    assert "const continuingActivityBlock = useMemo(() => {" in transcript
    assert 'continuing={continuingActivityBlock === index}' in transcript
    assert 'betweenSteps ? "is-between-steps" : ""' in transcript
    # ...but the quiet gap itself belongs to the one thinking capsule, shown only
    # once the gap is real, never to a second "continuing" label on the group.
    assert "继续处理中" not in transcript
    assert "LIVE_STATUS_GRACE_MS" in transcript
    assert "useSettledFlag(wantsCapsule, edge.started ? LIVE_STATUS_GRACE_MS : 0)" in transcript
    assert ".task-flow-group.is-running:not(.is-between-steps) .task-flow-group-icon::after {" in motion
    assert "loom-task-between-sheen" not in motion


def test_between_tool_handoff_keeps_completed_copy_semantics() -> None:
    transcript = TRANSCRIPT.read_text(encoding="utf-8")

    assert "const betweenSteps = Boolean(running && continuing && !hasActiveRows);" in transcript
    assert "const title = copy.groupTitle(categories, running && !betweenSteps);" in transcript


def test_inline_task_detail_preserves_content_through_collapse() -> None:
    transcript = TRANSCRIPT.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    assert "const detailPresence = useMotionPresence(open, 260);" in transcript
    assert 'const cachedDetailRef = useRef("");' in transcript
    assert "const visibleDetail = open ? liveDetail : cachedDetailRef.current;" in transcript
    assert "detailPresence.mounted ? (" in transcript
    assert "data-motion-phase={detailPresence.phase}" in transcript

    closed_start = motion.index(".task-flow-inline-detail-grid:not(.open) {")
    open_start = motion.index(".task-flow-inline-detail-grid.open {", closed_start)
    closed = motion[closed_start:open_start]
    assert "grid-template-rows 260ms" in closed
    assert "opacity 150ms ease 62ms" in closed
    assert "overflow-anchor: none;" in motion


def test_jump_to_latest_yields_to_transient_composer_surfaces() -> None:
    source = (ROOT / "desktop-react" / "src" / "components" / "transcript-scroll-stability.css").read_text(encoding="utf-8")

    jump_start = source.index(".transcript-jump-latest {")
    jump_end = source.index(".transcript-jump-latest.is-visible", jump_start)
    jump_rule = source[jump_start:jump_end]
    assert "z-index: 4;" in jump_rule
    assert "z-index: 32;" not in jump_rule

    occlusion_start = source.index(".workspace[data-composer-popover] .transcript-jump-latest {")
    occlusion_end = source.index("}", occlusion_start)
    occlusion_rule = source[occlusion_start:occlusion_end]
    assert "opacity: 0;" in occlusion_rule
    assert "pointer-events: none;" in occlusion_rule

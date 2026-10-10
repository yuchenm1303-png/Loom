import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
WEAVE_TSX = ROOT / "desktop-react" / "src" / "components" / "WeaveFlow.tsx"
WEAVE = ROOT / "desktop-react" / "src" / "components" / "weave.css"
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"
TURN_FLOW = ROOT / "desktop-react" / "src" / "components" / "turn-flow.css"
SCROLL = ROOT / "desktop-react" / "src" / "components" / "TranscriptScrollController.tsx"


def test_collapsed_activity_rows_do_not_reconcile_detail_streams() -> None:
    source = WEAVE_TSX.read_text(encoding="utf-8")

    assert "sameRowProps" in source
    assert "if (next.open) return rowDetail(previous.row, previous.delta) === rowDetail(next.row, next.delta);" in source
    # What a running command last printed is on its row; the rest of its output is not until it is opened.
    assert "if (rowTail(previous.row) !== rowTail(next.row)) return false;" in source
    assert "animationDelay" not in source
    assert "openRows.has(row.key)" in source


def test_task_motion_keeps_text_on_native_rasterization_layer() -> None:
    source = WEAVE.read_text(encoding="utf-8")

    fade_start = source.index("@keyframes wv-fade-in")
    fade_end = source.index("@keyframes wv-glyph-in", fade_start)
    fade = source[fade_start:fade_end]
    assert "transform:" not in fade and "translate" not in fade

    assert "will-change:" not in source
    assert "filter:" not in source.replace("filter: brightness(1.08);", "")
    # A finished animation must not pin a value over a transition that runs later.
    assert "wv-grow var(--wv-fold) var(--wv-ease) backwards" in source
    assert "wv-glyph-in 460ms var(--wv-spring) 60ms backwards" in source
    assert "wv-fade-in 220ms ease-out 90ms backwards" in source
    # Hovering a row lights its background; it never moves the copy.
    hover = source[source.index(".wv-row.is-expandable:hover {"):]
    assert "transform" not in hover[:hover.index("}")]
    assert "loom-task-icon-spring" in MOTION.read_text(encoding="utf-8")


def test_task_flow_copy_uses_whole_pixel_font_geometry() -> None:
    source = WEAVE.read_text(encoding="utf-8")

    def declarations(selector: str) -> str:
        start = source.index(selector + " {")
        return source[start:source.index("}", start)]

    verb = declarations(".wv-verb")
    assert "font-size: 13px;" in verb and "line-height: 20px;" in verb
    target = declarations(".wv-target")
    assert "font-size: 13px;" in target and "line-height: 20px;" in target
    assert "font-size: 12px;" in declarations(".wv-target.is-code")
    # Combined with native page zoom, fractional sizes soften glyph edges on Windows: every size the log sets is whole.
    sizes = re.findall(r"font-size:\s*([0-9.]+)px", source)
    assert sizes and all(float(size).is_integer() for size in sizes), sizes


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
    weave = WEAVE.read_text(encoding="utf-8")

    # The latest stage stays the motion anchor between tool batches, so rows
    # appended later are still born live...
    assert "const activeActivityBlocks = useMemo(() => {" in transcript
    assert "const latestActivityBlockIndex = useMemo(() => {" in transcript
    assert "const continuingActivityBlock = useMemo(() => {" in transcript
    assert "keepOpen={activeActivityBlocks.has(index) || continuingActivityBlock === index}" in transcript
    # ...but the quiet gap itself belongs to the one thinking capsule, shown only
    # once the gap is real, never to a second "continuing" label on the stage.
    assert "继续处理中" not in transcript
    assert "is-between-steps" not in transcript and "is-between-steps" not in weave
    assert "LIVE_STATUS_GRACE_MS" in transcript
    assert "useSettledFlag(wantsCapsule, edge.started ? LIVE_STATUS_GRACE_MS : 0)" in transcript
    assert "loom-task-between-sheen" not in weave


def test_only_a_step_that_is_running_looks_busy() -> None:
    weave = WEAVE.read_text(encoding="utf-8")
    flow = WEAVE_TSX.read_text(encoding="utf-8")
    model = (WEAVE_TSX.parent / "activityModel.ts").read_text(encoding="utf-8")

    # The live look is a property of a step's own state, never of its stage being the latest one.
    assert "if (isExecutingActivityStatus(status)) return \"running\";" in model
    assert 'data-tone={tone}' in flow
    assert ".wv-step[data-tone=\"running\"] .wv-halo," in weave
    assert ".wv-stage.is-running" not in weave
    assert ".wv-row.is-executing .wv-verb {" in weave


def test_inline_task_detail_preserves_content_through_collapse() -> None:
    flow = WEAVE_TSX.read_text(encoding="utf-8")
    weave = WEAVE.read_text(encoding="utf-8")

    assert "const detailPresence = useMotionPresence(open, 260);" in flow
    assert 'const cachedDetailRef = useRef("");' in flow
    assert "const visibleDetail = open ? liveDetail : cachedDetailRef.current;" in flow
    assert "detailPresence.mounted ? (" in flow
    assert "data-motion-phase={detailPresence.phase}" in flow

    # Closing keeps the pixels alive while the track shrinks and fades first: the words go quickly, the
    # space follows on the same curve as every other fold.
    closed = weave[weave.index(".wv-fold,\n.wv-sub-fold,\n.wv-detail {"):]
    closed = closed[:closed.index("}")]
    assert "grid-template-rows var(--wv-fold) var(--wv-ease)" in closed
    assert "opacity 140ms ease" in closed


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

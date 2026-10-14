import { ChevronDown } from "./icons";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import { advanceFollow, snapScrollOffset, TargetFlow, type FollowState } from "./followMotion";
import "./transcript-scroll-stability.css";

interface TranscriptScrollControllerProps {
  items: TranscriptItem[];
  threadId?: string | null;
  currentTurnId?: string | null;
  running?: boolean;
  hasOlder?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?(): Promise<void> | void;
}

const HISTORY_LOAD_THRESHOLD_PX = 220;
const SCROLL_EPSILON_PX = 2;
// Anything but our own last write that moves the view (the reader, a clamp after a fold) restarts the glide from where
// the view really is. A write comes back rounded, so this is wider than rounding.
const EXTERNAL_MOVE_PX = 1.5;
// Farther than this from the live edge (or two viewports, if more) the view jumps: a thread swap, a big mount.
const GLIDE_MAX_GAP_PX = 900;
// A scroll event this soon after one of this loop's own writes is the write's echo.
const OWN_SCROLL_ECHO_MS = 120;
const LIVE_SETTLE_WINDOW_MS = 820;
const SEND_FOLLOW_WINDOW_MS = 360;
const USER_RETURN_INTENT_MS = 420;
// An input whose target is unclear (a key, a gesture over an output pane) only counts as heading up or down
// if the transcript itself moves that way right after it.
const USER_INPUT_INTENT_MS = 320;
// A step opened during the run is judged over its own motion (the longest detail fold is 260ms): if following the
// live edge would carry its header out of view, the reader is reading it; the header keeps this margin below the top.
const DISCLOSURE_MOTION_MS = 420;
const DISCLOSURE_TOP_MARGIN_PX = 16;
const FINAL_SETTLE_PIN_DELAY_MS = LIVE_SETTLE_WINDOW_MS + 90;
const PANEL_RESIZE_END_EVENT = "loom:panel-resize-end";
const PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit";

// One shared query: the follow loop asks every frame, and matchMedia() itself
// is not free.
const reducedMotionQuery = typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") : undefined;

function prefersReducedMotion(): boolean {
  return Boolean(reducedMotionQuery?.matches) || document.documentElement.dataset.loomReducedMotion === "true";
}

function transcriptScroller(): HTMLDivElement | null {
  return document.querySelector<HTMLDivElement>(".conversation-stage > .transcript-scroll");
}

/** Publish intent changes only, rather than an attribute mutation every frame. */
function publishFollowing(scroller: HTMLDivElement, following: boolean) {
  const value = following ? "true" : "false";
  if (scroller.dataset.following !== value) scroller.dataset.following = value;
}

function isPanelResizeActive(): boolean {
  return document.body.classList.contains("loom-panel-resizing")
    || document.body.classList.contains("loom-panel-motion");
}

function isAtBottom(scroller: HTMLDivElement): boolean {
  const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  return distance <= SCROLL_EPSILON_PX;
}

/** Has this element something to scroll? Geometry first: computed style is the dearer read. */
function canScrollVertically(node: Element): boolean {
  if (node.scrollHeight <= node.clientHeight + 1) return false;
  const overflow = getComputedStyle(node).overflowY;
  return overflow === "auto" || overflow === "scroll";
}

/**
 * Is there a pane inside the transcript, a command's output for one, under a gesture that started on `target`?
 * Then it is unclear who scrolls: Chrome has usually moved the pane already when the wheel event arrives, so its
 * scroll position cannot say whether it took the tick or the transcript did. The scroll that follows can.
 */
function nestedScrollerUnder(target: EventTarget | null, scroller: HTMLDivElement): boolean {
  for (let node = target instanceof Element ? target : null; node && node !== scroller; node = node.parentElement) {
    if (canScrollVertically(node)) return true;
  }
  return false;
}

/** Caret movement and value changes in a form control are not scrolling, whatever key they use. */
function isTextEntry(target: EventTarget | null): boolean {
  return target instanceof Element
    && (target.closest("input, textarea, select") !== null || (target instanceof HTMLElement && target.isContentEditable));
}

function latestUserMessageId(items: TranscriptItem[]): string {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item?.type === "user_message") return item.id;
  }
  return "";
}

function latestActivityItemId(items: TranscriptItem[]): string {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item?.type === "tool_call" || item?.type === "process" || item?.type === "file_edit") return item.id;
  }
  return "";
}

/**
 * Owns the conversation viewport policy without coupling it to message layout.
 *
 * The important distinction is between "the viewport is temporarily no longer
 * at the bottom because content grew" and "the user intentionally scrolled up".
 * Treating both as the same scroll event used to disable auto-follow during
 * thread swaps and large streaming/layout updates.
 *
 * Leaving the live edge is explicit, and it has to be real: a wheel or touch
 * gesture that will move the transcript upward, a navigation key followed by an
 * upward scroll, a scrollbar drag. A gesture that scrolls something else (a
 * command's output pane) or nothing at all (a transcript that still fits) keeps
 * following. Opening a step of the live run does not leave the edge either: the
 * view keeps following the bottom, but never carries the opened step's header
 * out of sight.
 *
 * Bottom sync work is coalesced to at most one rAF callback per frame so bursts
 * of tool/process/streaming updates do not force repeated synchronous layout.
 */
export function TranscriptScrollController({
  items,
  threadId,
  currentTurnId,
  running,
  hasOlder = false,
  loadingOlder = false,
  onLoadOlder,
}: TranscriptScrollControllerProps) {
  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    const workspace = scroller?.closest<HTMLElement>(".workspace");
    if (!scroller || !workspace) return;
    // Native scrollbars reserve real layout space. Match that space in the
    // fixed composer so both columns align even on narrow Windows windows.
    const syncGutter = () => {
      const inset = Math.max(0, scroller.offsetWidth - scroller.clientWidth) / 2;
      workspace.style.setProperty("--ws-scroll-inset", `${inset}px`);
    };
    syncGutter();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(syncGutter);
    observer?.observe(scroller);
    return () => {
      observer?.disconnect();
      workspace.style.removeProperty("--ws-scroll-inset");
    };
  }, [threadId]);

  const followingRef = useRef(true);
  const forceBottomRef = useRef(false);
  const lastThreadIdRef = useRef(String(threadId ?? ""));
  const lastTurnIdRef = useRef(String(currentTurnId ?? ""));
  const lastUserMessageIdRef = useRef("");
  const lastActivityItemIdRef = useRef("");
  const lastScrollTopRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const bottomTargetRef = useRef(0);
  const viewportHeightRef = useRef(0);
  const bottomTargetDirtyRef = useRef(true);
  const snapBottomRef = useRef(false);
  // The glide toward the live edge: where the spring is, what we last wrote (to tell our own scrolling from anyone
  // else's), when it last stepped, and whether it is still moving.
  const motionRef = useRef<FollowState>({ position: 0, velocity: 0 });
  const flowRef = useRef(new TargetFlow());
  const lastWriteRef = useRef<number | null>(null);
  const lastTickAtRef = useRef(0);
  const glidingRef = useRef(false);
  const travelRef = useRef(false);
  const jumpShownRef = useRef(false);
  const touchYRef = useRef<number | null>(null);
  const scrollbarPointerRef = useRef<number | null>(null);
  const userDetachedRef = useRef(false);
  const returnIntentUntilRef = useRef(0);
  const inputIntentRef = useRef({ up: false, until: 0 });
  // The step the reader just opened while the run was live, until its own motion is over.
  const openedStepRef = useRef<{ header: HTMLElement; until: number } | null>(null);
  const runningRef = useRef(Boolean(running));
  const wasRunningRef = useRef(Boolean(running));
  const settleUntilRef = useRef(0);
  const settleTimerRef = useRef<number | null>(null);
  const hasOlderRef = useRef(Boolean(hasOlder));
  const loadingOlderRef = useRef(Boolean(loadingOlder));
  const onLoadOlderRef = useRef(onLoadOlder);
  const itemsLengthRef = useRef(items.length);
  const historyLoadInFlightRef = useRef(false);
  const prependAnchorRef = useRef<{ threadId: string; scrollHeight: number; scrollTop: number; itemCount: number } | null>(null);
  const [jumpVisible, setJumpVisibleState] = useState(false);
  const setJumpVisible = (visible: boolean) => {
    jumpShownRef.current = visible;
    setJumpVisibleState(visible);
  };
  const latestUserId = useMemo(() => latestUserMessageId(items), [items]);
  const latestActivityId = useMemo(() => latestActivityItemId(items), [items]);

  hasOlderRef.current = Boolean(hasOlder);
  loadingOlderRef.current = Boolean(loadingOlder);
  onLoadOlderRef.current = onLoadOlder;
  itemsLengthRef.current = items.length;

  const cancelScheduledScroll = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  };

  const requestOlderHistory = (scroller: HTMLDivElement) => {
    const loader = onLoadOlderRef.current;
    if (
      !loader
      || !hasOlderRef.current
      || loadingOlderRef.current
      || historyLoadInFlightRef.current
      || scroller.scrollTop > HISTORY_LOAD_THRESHOLD_PX
    ) return;

    const anchor = {
      threadId: String(threadId ?? ""),
      scrollHeight: scroller.scrollHeight,
      scrollTop: scroller.scrollTop,
      itemCount: itemsLengthRef.current,
    };
    prependAnchorRef.current = anchor;
    historyLoadInFlightRef.current = true;
    Promise.resolve().then(() => loader())
      .catch(() => {
        // History paging is best-effort UI work. A later scroll gesture can retry.
      })
      .finally(() => {
        historyLoadInFlightRef.current = false;
        window.requestAnimationFrame(() => {
          if (
            prependAnchorRef.current === anchor
            && itemsLengthRef.current <= anchor.itemCount
          ) prependAnchorRef.current = null;
        });
      });
  };

  const detachFromLiveFollow = (scroller: HTMLDivElement) => {
    // Detachment is driven only by explicit user input (wheel/touch/scrollbar/
    // navigation keys). A raw scroll event is not intent: Chromium also emits
    // upward scroll events when streaming layout shrinks, task groups fold, or
    // scroll anchoring clamps the viewport.
    userDetachedRef.current = true;
    followingRef.current = false;
    publishFollowing(scroller, false);
    forceBottomRef.current = false;
    snapBottomRef.current = false;
    glidingRef.current = false;
    travelRef.current = false;
    lastWriteRef.current = null;
    flowRef.current.reset();
    returnIntentUntilRef.current = 0;
    releaseOpenedStep();
    cancelScheduledScroll();
    setJumpVisible(true);
  };

  const releaseOpenedStep = () => {
    openedStepRef.current = null;
  };

  /**
   * Where the bottom sync must stop so a step the reader just opened keeps its header on screen: just above
   * the header. Infinity when no such step is in its motion (closed, gone, or long enough ago that it
   * is no longer the click that is moving things).
   */
  const openedStepLimit = (scroller: HTMLDivElement): number => {
    const opened = openedStepRef.current;
    if (!opened) return Number.POSITIVE_INFINITY;
    if (
      performance.now() > opened.until
      || !opened.header.isConnected
      || opened.header.getAttribute("aria-expanded") !== "true"
    ) {
      releaseOpenedStep();
      return Number.POSITIVE_INFINITY;
    }
    const top = opened.header.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
    return Math.max(0, top - DISCLOSURE_TOP_MARGIN_PX);
  };

  const markReturnIntent = () => {
    returnIntentUntilRef.current = performance.now() + USER_RETURN_INTENT_MS;
  };

  /**
   * One step of the viewport toward the live edge. It runs from the ResizeObserver, which fires after layout and
   * before paint, so a step of new content is answered in the frame that first shows it (a frame scheduled from the
   * observer would show it, unscrolled, for one frame first), and from the animation frame that carries a glide on.
   * Returns whether the view is still travelling and another frame is needed.
   */
  const followTick = (scroller: HTMLDivElement, fromObserver: boolean): boolean => {
    if (isPanelResizeActive()) return false;
    const forced = forceBottomRef.current;
    const snapNow = snapBottomRef.current;
    forceBottomRef.current = false;
    snapBottomRef.current = false;
    if (!followingRef.current && !forced) return false;
    const now = performance.now();
    const motion = motionRef.current;

    // Layout is read when it changed (the observer says so) or is asked for; between those the glide uses the last
    // reading, so a long glide over an unchanged page measures nothing.
    if (fromObserver || bottomTargetDirtyRef.current) {
      viewportHeightRef.current = scroller.clientHeight;
      bottomTargetRef.current = Math.max(0, scroller.scrollHeight - viewportHeightRef.current);
      bottomTargetDirtyRef.current = false;
      flowRef.current.note(bottomTargetRef.current, now);
      const actual = scroller.scrollTop;
      const written = lastWriteRef.current;
      const moved = written === null || Math.abs(actual - written) > EXTERNAL_MOVE_PX;
      // The page is tall enough for where the view was, yet the browser moved it up: a layout in the middle of a DOM
      // commit (React removes a node before it inserts its replacement) saw a briefly shorter page and clamped the
      // view to it. Nobody scrolled, so the glide is left where it was: the write below puts the view back before the
      // frame is drawn, and the jump never is.
      const clamped = written !== null && actual < written
        && bottomTargetRef.current >= written - EXTERNAL_MOVE_PX
        && scrollbarPointerRef.current === null
        && now > inputIntentRef.current.until;
      // Otherwise something other than this loop moved it (the page really shrank under the view, or the reader
      // dragged it): start from where it is.
      if (moved && !clamped) {
        motion.position = actual;
        motion.velocity = 0;
      }
    }
    // Following the live edge would carry the step the reader just opened out of view. They opened it to
    // read it: stop on its header, as if they had scrolled up to it.
    const openLimit = openedStepLimit(scroller);
    if (bottomTargetRef.current > openLimit + SCROLL_EPSILON_PX) {
      if (Math.abs(scroller.scrollTop - openLimit) > 0.01) scroller.scrollTop = openLimit;
      lastScrollTopRef.current = scroller.scrollTop;
      detachFromLiveFollow(scroller);
      return false;
    }
    const target = bottomTargetRef.current;
    const gap = Math.abs(target - motion.position);
    // Navigation and completion take the longer glide; the live edge of a running turn the short one.
    const travelling = travelRef.current || (!runningRef.current && now < settleUntilRef.current);
    let settled: boolean;
    if (snapNow || prefersReducedMotion() || gap > Math.max(GLIDE_MAX_GAP_PX, viewportHeightRef.current * 2)) {
      motion.position = target;
      motion.velocity = 0;
      settled = true;
    } else {
      // The first step after a rest starts from standstill: the time since the last step was spent standing still.
      const seconds = glidingRef.current ? (now - lastTickAtRef.current) / 1000 : 0;
      // Content growing frame by frame is stayed with even while travelling: the gentle glide is for a view that is
      // going somewhere, not for one whose bottom edge is being drawn out under it.
      const flowing = flowRef.current.flowing(now);
      const next = advanceFollow(motion, target, seconds, travelling && !flowing ? "travel" : "live", flowing);
      motion.position = next.position;
      motion.velocity = next.velocity;
      settled = next.settled;
    }
    lastTickAtRef.current = now;
    glidingRef.current = !settled;
    if (settled) travelRef.current = false;

    const written = Math.min(target, Math.max(0, snapScrollOffset(motion.position, window.devicePixelRatio)));
    if (lastWriteRef.current === null || Math.abs(written - lastWriteRef.current) > 0.001 || Math.abs(scroller.scrollTop - written) > 0.001) {
      scroller.scrollTop = written;
    }
    // CSS zoom, fractional device pixels and a layout clamp can change the
    // applied offset. Ownership tracks the browser's result, not our request.
    const applied = scroller.scrollTop;
    lastWriteRef.current = applied;
    lastScrollTopRef.current = applied;
    followingRef.current = true;
    publishFollowing(scroller, true);
    if (jumpShownRef.current) {
      jumpShownRef.current = false;
      setJumpVisible(false);
    }
    return !settled;
  };

  const followFrame = () => {
    frameRef.current = null;
    const scroller = transcriptScroller();
    if (!scroller) return;
    if (followTick(scroller, false)) frameRef.current = requestAnimationFrame(followFrame);
  };

  const scheduleBottomSync = (scroller: HTMLDivElement, force = false, snap = false) => {
    bottomTargetDirtyRef.current = true;
    if (force) {
      forceBottomRef.current = true;
      travelRef.current = true;
    }
    if (snap) snapBottomRef.current = true;
    if (isPanelResizeActive() || frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(followFrame);
  };

  useLayoutEffect(() => {
    const anchor = prependAnchorRef.current;
    if (!anchor || anchor.threadId !== String(threadId ?? "") || items.length <= anchor.itemCount) return;
    const scroller = transcriptScroller();
    if (!scroller) return;

    const addedHeight = Math.max(0, scroller.scrollHeight - anchor.scrollHeight);
    scroller.scrollTop = anchor.scrollTop + addedHeight;
    lastScrollTopRef.current = scroller.scrollTop;
    bottomTargetDirtyRef.current = true;
    prependAnchorRef.current = null;
  }, [items.length, threadId]);

  useLayoutEffect(() => {
    const nextRunning = Boolean(running);
    runningRef.current = nextRunning;

    if (settleTimerRef.current !== null) {
      window.clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }

    if (wasRunningRef.current && !nextRunning) {
      // Completion changes height in both directions: the live process stack
      // folds while the final answer/actions/artifacts finish mounting. Follow
      // those mutations continuously, then do one exact final pin. The final
      // pin is conditional on the user still being attached, so an intentional
      // scroll-up during handoff is never overridden.
      settleUntilRef.current = performance.now() + LIVE_SETTLE_WINDOW_MS;
      const scroller = transcriptScroller();
      if (scroller && followingRef.current && !userDetachedRef.current) {
        scheduleBottomSync(scroller);
      }
      settleTimerRef.current = window.setTimeout(() => {
        settleTimerRef.current = null;
        const latestScroller = transcriptScroller();
        if (!latestScroller || !followingRef.current || userDetachedRef.current) return;
        scheduleBottomSync(latestScroller, false, true);
      }, FINAL_SETTLE_PIN_DELAY_MS);
    } else if (nextRunning) {
      settleUntilRef.current = 0;
    }

    wasRunningRef.current = nextRunning;

    return () => {
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current);
        settleTimerRef.current = null;
      }
    };
  }, [running]);

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    if (!scroller) return;

    const nextThreadId = String(threadId ?? "");
    const nextTurnId = String(currentTurnId ?? "");
    const threadChanged = nextThreadId !== lastThreadIdRef.current;
    const turnChanged = Boolean(nextTurnId) && nextTurnId !== lastTurnIdRef.current;
    const userMessageAdded = Boolean(latestUserId) && latestUserId !== lastUserMessageIdRef.current;
    const activityAdded = Boolean(latestActivityId) && latestActivityId !== lastActivityItemIdRef.current;

    if (threadChanged) {
      // A real thread replacement should still pin before paint. Animating from
      // the previous conversation's scroll position would expose stale geometry.
      userDetachedRef.current = false;
      followingRef.current = true;
      publishFollowing(scroller, true);
      returnIntentUntilRef.current = 0;
      releaseOpenedStep();
      setJumpVisible(false);
      scheduleBottomSync(scroller, true, true);
    } else if (turnChanged || userMessageAdded) {
      // Sending is different from swapping conversations: the user is already
      // looking at this viewport. Keep it force-following, but let the bottom
      // anchor travel through the same short spring as the bubble entrance so
      // composer collapse + message insertion read as one continuous motion.
      settleUntilRef.current = Math.max(
        settleUntilRef.current,
        performance.now() + SEND_FOLLOW_WINDOW_MS,
      );
      userDetachedRef.current = false;
      followingRef.current = true;
      publishFollowing(scroller, true);
      returnIntentUntilRef.current = 0;
      releaseOpenedStep();
      setJumpVisible(false);
      scheduleBottomSync(scroller, true);
    } else if (activityAdded && followingRef.current) {
      // Let new task rows and the viewport share the same spring. The previous
      // hard snap made a polished row entrance look like the whole transcript
      // jumped when several tools arrived together.
      scheduleBottomSync(scroller);
    }
    // Any other growth reaches the viewport through the ResizeObserver below, after layout and in the same frame.

    lastThreadIdRef.current = nextThreadId;
    lastTurnIdRef.current = nextTurnId;
    lastUserMessageIdRef.current = latestUserId;
    lastActivityItemIdRef.current = latestActivityId;
  }, [items, latestUserId, latestActivityId, threadId, currentTurnId, running]);

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    const content = scroller?.querySelector<HTMLElement>(".transcript");
    if (!scroller || !content) return;

    userDetachedRef.current = false;
    followingRef.current = true;
    publishFollowing(scroller, true);
    forceBottomRef.current = true;
    returnIntentUntilRef.current = 0;
    releaseOpenedStep();
    lastScrollTopRef.current = scroller.scrollTop;
    setJumpVisible(false);
    // Mount/thread swaps are authoritative navigation and should not visibly
    // travel from whatever scroll position belonged to the previous subtree.
    scheduleBottomSync(scroller, true, true);

    const resumeIfUserReturnedToBottom = () => {
      if (
        userDetachedRef.current
        && isAtBottom(scroller)
        && performance.now() <= returnIntentUntilRef.current
      ) {
        userDetachedRef.current = false;
        followingRef.current = true;
        publishFollowing(scroller, true);
        returnIntentUntilRef.current = 0;
        setJumpVisible(false);
        // The reader has already arrived. Adopt the physical edge without
        // restarting a travel spring from the position we detached at.
        scheduleBottomSync(scroller, false, true);
        return true;
      }
      return false;
    };

    const onScroll = () => {
      if (isPanelResizeActive()) return;
      // Every step of the glide fires one of these a frame later. While nothing else is steering (no key, no
      // scrollbar drag, no gesture over an output pane) and this loop wrote a moment ago, it is its own echo: skip
      // reading scrollTop, which would force layout early in the frame for nothing.
      const now = performance.now();
      if (
        followingRef.current
        && !userDetachedRef.current
        && lastWriteRef.current !== null
        && now - lastTickAtRef.current < OWN_SCROLL_ECHO_MS
        && scrollbarPointerRef.current === null
        && now > inputIntentRef.current.until
      ) return;

      const nextScrollTop = scroller.scrollTop;
      const movedUp = nextScrollTop < lastScrollTopRef.current - SCROLL_EPSILON_PX;

      // Never infer user intent from direction alone. Streaming Markdown,
      // collapsing task groups and browser scroll anchoring can all move
      // scrollTop upward without any human input. A move is the reader's only
      // when the input that caused it is known: a scrollbar drag (its pointer
      // is explicit) or a key / gesture over an output pane a moment ago (their
      // targets do not say which pane scrolls; the scroll does). A wheel or
      // touch over plain content is decided when it arrives, before this event.
      const intent = inputIntentRef.current;
      const recent = performance.now() <= intent.until;
      if (movedUp && (scrollbarPointerRef.current !== null || (recent && intent.up))) {
        detachFromLiveFollow(scroller);
      } else {
        if (recent && !intent.up && nextScrollTop > lastScrollTopRef.current + SCROLL_EPSILON_PX) releaseOpenedStep();
        resumeIfUserReturnedToBottom();
      }

      lastScrollTopRef.current = nextScrollTop;
      setJumpVisible(userDetachedRef.current);
      if (nextScrollTop <= HISTORY_LOAD_THRESHOLD_PX) requestOlderHistory(scroller);
    };

    // Which way a wheel or touch gesture heads, and whether it is the transcript's business at all.
    const onWheel = (event: WheelEvent) => {
      if (isPanelResizeActive() || event.ctrlKey || !event.deltaY) return; // ctrl+wheel zooms the page
      steer(event.target, event.deltaY < 0);
    };

    const steer = (target: EventTarget | null, up: boolean) => {
      if (nestedScrollerUnder(target, scroller)) {
        // Unclear who scrolls: remember the heading and let onScroll decide.
        inputIntentRef.current = { up, until: performance.now() + USER_INPUT_INTENT_MS };
        if (!up) markReturnIntent();
      } else if (up) {
        // Pulling at a transcript that has nothing to scroll is not leaving the live edge.
        if (canScrollVertically(scroller)) detachFromLiveFollow(scroller);
        requestOlderHistory(scroller);
      } else {
        releaseOpenedStep();
        markReturnIntent();
        resumeIfUserReturnedToBottom();
      }
    };

    const onTouchStart = (event: TouchEvent) => {
      touchYRef.current = event.touches[0]?.clientY ?? null;
    };

    const onTouchMove = (event: TouchEvent) => {
      if (isPanelResizeActive()) return;
      const nextY = event.touches[0]?.clientY;
      const previousY = touchYRef.current;
      if (nextY == null) return;
      if (previousY != null) {
        // Finger down reveals older content; finger up heads toward latest.
        if (nextY > previousY + SCROLL_EPSILON_PX) steer(event.target, true);
        else if (nextY < previousY - SCROLL_EPSILON_PX) steer(event.target, false);
      }
      touchYRef.current = nextY;
    };

    const onTouchEnd = () => {
      touchYRef.current = null;
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || scroller.scrollHeight <= scroller.clientHeight) return;
      const rect = scroller.getBoundingClientRect();
      const nativeGutter = Math.max(0, scroller.offsetWidth - scroller.clientWidth);
      const hitWidth = Math.max(12, nativeGutter + 3);
      if (event.clientX >= rect.right - hitWidth) {
        scrollbarPointerRef.current = event.pointerId;
        detachFromLiveFollow(scroller);
      }
    };

    // Overlay scrollbars live outside this DOM subtree. Their explicit events
    // transfer ownership before the first write, including track clicks and
    // keyboard scrolling. A nested output pane must not detach the transcript.
    const onCustomScrollbarStart = (event: Event) => {
      if (event.target !== scroller) return;
      detachFromLiveFollow(scroller);
    };
    const onCustomScrollbarEnd = (event: Event) => {
      if (event.target !== scroller
        || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > SCROLL_EPSILON_PX) return;
      markReturnIntent();
      resumeIfUserReturnedToBottom();
    };

    const finishScrollbarPointer = (event: PointerEvent) => {
      if (scrollbarPointerRef.current !== event.pointerId) return;
      scrollbarPointerRef.current = null;
      if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= SCROLL_EPSILON_PX) {
        markReturnIntent();
        resumeIfUserReturnedToBottom();
      }
    };

    // With nothing focused the keyboard still scrolls the pane the reader last clicked, and the key's target
    // (the body) does not say which. So a key only records intent; the scroll it causes, if it is ours, decides.
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTextEntry(event.target) || event.altKey || event.metaKey) return;

      if (
        event.key === "PageUp"
        || event.key === "Home"
        || event.key === "ArrowUp"
        || (event.key === " " && event.shiftKey)
      ) {
        inputIntentRef.current = { up: true, until: performance.now() + USER_INPUT_INTENT_MS };
        requestOlderHistory(scroller);
      } else if (
        event.key === "PageDown"
        || event.key === "End"
        || event.key === "ArrowDown"
        || (event.key === " " && !event.shiftKey)
      ) {
        inputIntentRef.current = { up: false, until: performance.now() + USER_INPUT_INTENT_MS };
        markReturnIntent();
        resumeIfUserReturnedToBottom();
      }
    };

    const onPanelLayoutCommit = () => {
      if (!followingRef.current && !forceBottomRef.current) return;
      // Track width changes now happen once rather than on every animation
      // frame. Re-pin the bottom in this layout phase so a long transcript does
      // not visibly jump upward when its line wrapping changes.
      cancelScheduledScroll();
      releaseOpenedStep();
      scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      lastScrollTopRef.current = scroller.scrollTop;
      motionRef.current = { position: scroller.scrollTop, velocity: 0 };
      lastWriteRef.current = scroller.scrollTop;
      glidingRef.current = false;
      followingRef.current = true;
      publishFollowing(scroller, true);
      forceBottomRef.current = false;
      snapBottomRef.current = false;
      bottomTargetDirtyRef.current = true;
      setJumpVisible(false);
    };

    const onPanelResizeEnd = () => {
      if (followingRef.current || forceBottomRef.current) scheduleBottomSync(scroller);
    };

    // Opening something is a decision about where the reader looks, taken before React changes any height
    // (delegated click also covers keyboard activation and rapid reversals).
    // - A step of the run that is still going: the reader is watching it work. Following the bottom is exactly
    //   what they expect, so it continues; only if that would push the step they opened out of view do they
    //   stay on it (see openedStepLimit).
    // - Anything else (a finished turn's process, a decision card): the reader opened history in place. Like
    //   scrolling up, that ends the live edge, and the fold must not be chased by a bottom follow.
    // Closing never needs a decision: content only shrinks under a view that is already where it should be.
    const onDisclosureClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const disclosure = target?.closest<HTMLButtonElement>("button[aria-expanded]");
      if (!disclosure || disclosure.disabled || !content.contains(disclosure)) return;
      if (!followingRef.current || disclosure.getAttribute("aria-expanded") === "true") return;
      if (runningRef.current && disclosure.closest(".turn-block.is-active")) {
        openedStepRef.current = { header: disclosure, until: performance.now() + DISCLOSURE_MOTION_MS };
        return;
      }
      detachFromLiveFollow(scroller);
    };

    scroller.addEventListener("click", onDisclosureClick, true);
    scroller.addEventListener("scroll", onScroll, { passive: true });
    scroller.addEventListener("wheel", onWheel, { passive: true });
    scroller.addEventListener("touchstart", onTouchStart, { passive: true });
    scroller.addEventListener("touchmove", onTouchMove, { passive: true });
    scroller.addEventListener("touchend", onTouchEnd, { passive: true });
    scroller.addEventListener("touchcancel", onTouchEnd, { passive: true });
    scroller.addEventListener("pointerdown", onPointerDown, { passive: true });
    scroller.addEventListener("loom:scrollbar-drag-start", onCustomScrollbarStart);
    scroller.addEventListener("loom:scrollbar-drag-end", onCustomScrollbarEnd);
    window.addEventListener("pointerup", finishScrollbarPointer, { passive: true });
    window.addEventListener("pointercancel", finishScrollbarPointer, { passive: true });
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener(PANEL_LAYOUT_COMMIT_EVENT, onPanelLayoutCommit);
    window.addEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);

    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (followingRef.current || forceBottomRef.current) {
            // Layout is current here and the frame has not been painted: step now, finish the glide in frames.
            if (followTick(scroller, true) && frameRef.current === null && !isPanelResizeActive()) {
              frameRef.current = requestAnimationFrame(followFrame);
            }
          } else {
            // Streaming can move the bottom without a scroll event while the
            // user is reading history. Keep the return-to-latest affordance in
            // sync without re-enabling follow.
            setJumpVisible(userDetachedRef.current);
          }
        });

    observer?.observe(scroller);
    observer?.observe(content);

    return () => {
      scroller.removeEventListener("click", onDisclosureClick, true);
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("wheel", onWheel);
      scroller.removeEventListener("touchstart", onTouchStart);
      scroller.removeEventListener("touchmove", onTouchMove);
      scroller.removeEventListener("touchend", onTouchEnd);
      scroller.removeEventListener("touchcancel", onTouchEnd);
      scroller.removeEventListener("pointerdown", onPointerDown);
      scroller.removeEventListener("loom:scrollbar-drag-start", onCustomScrollbarStart);
      scroller.removeEventListener("loom:scrollbar-drag-end", onCustomScrollbarEnd);
      window.removeEventListener("pointerup", finishScrollbarPointer);
      window.removeEventListener("pointercancel", finishScrollbarPointer);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener(PANEL_LAYOUT_COMMIT_EVENT, onPanelLayoutCommit);
      window.removeEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);
      observer?.disconnect();
      cancelScheduledScroll();
    };
  }, [threadId]);

  const jumpToLatest = () => {
    const scroller = transcriptScroller();
    if (!scroller) return;

    userDetachedRef.current = false;
    followingRef.current = true;
    publishFollowing(scroller, true);
    forceBottomRef.current = false;
    returnIntentUntilRef.current = 0;
    releaseOpenedStep();
    setJumpVisible(false);

    // Reuse the same owned follow loop instead of starting a second native
    // smooth-scroll animation that can race ResizeObserver during streaming.
    scheduleBottomSync(scroller, true, prefersReducedMotion());
  };

  return (
    <button
      type="button"
      className={`transcript-jump-latest ${jumpVisible ? "is-visible" : ""}`}
      onClick={jumpToLatest}
      aria-label="回到最新消息"
      title="回到最新消息"
      aria-hidden={!jumpVisible}
      tabIndex={jumpVisible ? 0 : -1}
    >
      <ChevronDown size={20} strokeWidth={1.8} aria-hidden="true" />
    </button>
  );
}

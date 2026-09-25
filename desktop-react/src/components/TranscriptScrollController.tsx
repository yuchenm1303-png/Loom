import { ChevronDown } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import "./transcript-scroll-stability.css";

interface TranscriptScrollControllerProps {
  items: TranscriptItem[];
  threadId?: string | null;
  currentTurnId?: string | null;
  running?: boolean;
}

const BOTTOM_THRESHOLD_PX = 96;
const SCROLL_EPSILON_PX = 2;
const LIVE_FOLLOW_MIN_STEP_PX = 1.5;
const LIVE_FOLLOW_MAX_STEP_PX = 42;
const LIVE_FOLLOW_NEAR_EASE = 0.22;
const LIVE_FOLLOW_FAR_EASE = 0.38;
const LIVE_FOLLOW_PRESSURE_PX = 420;
const LIVE_SETTLE_WINDOW_MS = 820;
const SEND_FOLLOW_WINDOW_MS = 360;
const USER_RETURN_INTENT_MS = 420;
const FINAL_SETTLE_PIN_DELAY_MS = LIVE_SETTLE_WINDOW_MS + 90;
const PANEL_RESIZE_END_EVENT = "loom:panel-resize-end";
const PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit";

function transcriptScroller(): HTMLDivElement | null {
  return document.querySelector<HTMLDivElement>(".conversation-stage > .transcript-scroll");
}

function isPanelResizeActive(): boolean {
  return document.body.classList.contains("loom-panel-resizing")
    || document.body.classList.contains("loom-panel-motion");
}

function isNearBottom(scroller: HTMLDivElement): boolean {
  const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  return distance <= BOTTOM_THRESHOLD_PX;
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
 * Bottom sync work is coalesced to at most one rAF callback per frame so bursts
 * of tool/process/streaming updates do not force repeated synchronous layout.
 */
export function TranscriptScrollController({
  items,
  threadId,
  currentTurnId,
  running,
}: TranscriptScrollControllerProps) {
  const followingRef = useRef(true);
  const forceBottomRef = useRef(false);
  const lastThreadIdRef = useRef(String(threadId ?? ""));
  const lastTurnIdRef = useRef(String(currentTurnId ?? ""));
  const lastUserMessageIdRef = useRef("");
  const lastActivityItemIdRef = useRef("");
  const lastScrollTopRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const snapBottomRef = useRef(false);
  const touchYRef = useRef<number | null>(null);
  const scrollbarPointerRef = useRef<number | null>(null);
  const userDetachedRef = useRef(false);
  const returnIntentUntilRef = useRef(0);
  const runningRef = useRef(Boolean(running));
  const wasRunningRef = useRef(Boolean(running));
  const settleUntilRef = useRef(0);
  const settleTimerRef = useRef<number | null>(null);
  const [jumpVisible, setJumpVisible] = useState(false);
  const latestUserId = useMemo(() => latestUserMessageId(items), [items]);
  const latestActivityId = useMemo(() => latestActivityItemId(items), [items]);

  const cancelScheduledScroll = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  };

  const detachFromLiveFollow = (scroller: HTMLDivElement) => {
    // Detachment is driven only by explicit user input (wheel/touch/scrollbar/
    // navigation keys). A raw scroll event is not intent: Chromium also emits
    // upward scroll events when streaming layout shrinks, task groups fold, or
    // scroll anchoring clamps the viewport.
    userDetachedRef.current = true;
    followingRef.current = false;
    forceBottomRef.current = false;
    snapBottomRef.current = false;
    returnIntentUntilRef.current = 0;
    cancelScheduledScroll();
    setJumpVisible(!isNearBottom(scroller));
  };

  const markReturnIntent = () => {
    returnIntentUntilRef.current = performance.now() + USER_RETURN_INTENT_MS;
  };

  const scheduleBottomSync = (scroller: HTMLDivElement, force = false, snap = false) => {
    if (force) forceBottomRef.current = true;
    if (snap) snapBottomRef.current = true;
    if (isPanelResizeActive() || frameRef.current !== null) return;

    const step = () => {
      frameRef.current = null;
      if (isPanelResizeActive()) return;

      const forced = forceBottomRef.current;
      const snapNow = snapBottomRef.current;
      forceBottomRef.current = false;
      snapBottomRef.current = false;
      if (!followingRef.current && !forced) return;

      const target = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const distance = target - scroller.scrollTop;
      const absoluteDistance = Math.abs(distance);
      const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
        || document.documentElement.dataset.loomReducedMotion === "true";
      const liveMotion = runningRef.current || performance.now() < settleUntilRef.current;
      const easeLiveGrowth = Boolean(
        (liveMotion || forced)
        && !snapNow
        && !reducedMotion
        && absoluteDistance > SCROLL_EPSILON_PX
      );

      if (easeLiveGrowth) {
        const pressure = Math.min(1, absoluteDistance / LIVE_FOLLOW_PRESSURE_PX);
        const ease = LIVE_FOLLOW_NEAR_EASE
          + (LIVE_FOLLOW_FAR_EASE - LIVE_FOLLOW_NEAR_EASE) * pressure;
        const maxStep = 24 + (LIVE_FOLLOW_MAX_STEP_PX - 24) * pressure;
        const delta = Math.min(
          maxStep,
          Math.max(LIVE_FOLLOW_MIN_STEP_PX, absoluteDistance * ease),
        );
        scroller.scrollTop = distance >= 0
          ? Math.min(target, scroller.scrollTop + delta)
          : Math.max(target, scroller.scrollTop - delta);
      } else {
        scroller.scrollTop = target;
      }

      lastScrollTopRef.current = scroller.scrollTop;
      followingRef.current = true;
      setJumpVisible(false);

      if (easeLiveGrowth && Math.abs(target - scroller.scrollTop) > SCROLL_EPSILON_PX) {
        frameRef.current = requestAnimationFrame(step);
      }
    };

    frameRef.current = requestAnimationFrame(step);
  };

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
      returnIntentUntilRef.current = 0;
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
      returnIntentUntilRef.current = 0;
      setJumpVisible(false);
      scheduleBottomSync(scroller, true);
    } else if (activityAdded && followingRef.current) {
      // Let new task rows and the viewport share the same spring. The previous
      // hard snap made a polished row entrance look like the whole transcript
      // jumped when several tools arrived together.
      scheduleBottomSync(scroller);
    } else if (followingRef.current) {
      scheduleBottomSync(scroller);
    }

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
    forceBottomRef.current = true;
    returnIntentUntilRef.current = 0;
    lastScrollTopRef.current = scroller.scrollTop;
    setJumpVisible(false);
    // Mount/thread swaps are authoritative navigation and should not visibly
    // travel from whatever scroll position belonged to the previous subtree.
    scheduleBottomSync(scroller, true, true);

    const resumeIfUserReturnedToBottom = () => {
      if (
        userDetachedRef.current
        && isNearBottom(scroller)
        && performance.now() <= returnIntentUntilRef.current
      ) {
        userDetachedRef.current = false;
        followingRef.current = true;
        returnIntentUntilRef.current = 0;
        setJumpVisible(false);
        scheduleBottomSync(scroller);
        return true;
      }
      return false;
    };

    const onScroll = () => {
      if (isPanelResizeActive()) return;

      const nextScrollTop = scroller.scrollTop;
      const nearBottom = isNearBottom(scroller);
      const movedUp = nextScrollTop < lastScrollTopRef.current - SCROLL_EPSILON_PX;

      // Never infer user intent from direction alone. Streaming Markdown,
      // collapsing task groups and browser scroll anchoring can all move
      // scrollTop upward without any human input. Only a scrollbar drag is
      // diagnosed here because its pointer provenance is explicit; wheel/touch
      // and keyboard intent are captured before their scroll event.
      if (scrollbarPointerRef.current !== null && movedUp) {
        detachFromLiveFollow(scroller);
      } else {
        resumeIfUserReturnedToBottom();
      }

      lastScrollTopRef.current = nextScrollTop;
      setJumpVisible(userDetachedRef.current && !nearBottom);
    };

    const onWheel = (event: WheelEvent) => {
      if (isPanelResizeActive()) return;
      if (event.deltaY < 0) {
        detachFromLiveFollow(scroller);
      } else if (event.deltaY > 0) {
        markReturnIntent();
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
        if (nextY > previousY + SCROLL_EPSILON_PX) {
          detachFromLiveFollow(scroller);
        } else if (nextY < previousY - SCROLL_EPSILON_PX) {
          markReturnIntent();
        }
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
      }
    };

    const finishScrollbarPointer = (event: PointerEvent) => {
      if (scrollbarPointerRef.current !== event.pointerId) return;
      scrollbarPointerRef.current = null;
      if (isNearBottom(scroller)) {
        markReturnIntent();
        resumeIfUserReturnedToBottom();
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.matches("input, textarea, select")
        || target?.isContentEditable
        || event.defaultPrevented
      ) return;

      if (
        event.key === "PageUp"
        || event.key === "Home"
        || event.key === "ArrowUp"
        || (event.key === " " && event.shiftKey)
      ) {
        detachFromLiveFollow(scroller);
      } else if (
        event.key === "PageDown"
        || event.key === "End"
        || event.key === "ArrowDown"
        || (event.key === " " && !event.shiftKey)
      ) {
        markReturnIntent();
      }
    };

    const onPanelLayoutCommit = () => {
      if (!followingRef.current && !forceBottomRef.current) return;
      // Track width changes now happen once rather than on every animation
      // frame. Re-pin the bottom in this layout phase so a long transcript does
      // not visibly jump upward when its line wrapping changes.
      cancelScheduledScroll();
      scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      lastScrollTopRef.current = scroller.scrollTop;
      followingRef.current = true;
      forceBottomRef.current = false;
      snapBottomRef.current = false;
      setJumpVisible(false);
    };

    const onPanelResizeEnd = () => {
      if (followingRef.current || forceBottomRef.current) scheduleBottomSync(scroller);
    };

    scroller.addEventListener("scroll", onScroll, { passive: true });
    scroller.addEventListener("wheel", onWheel, { passive: true });
    scroller.addEventListener("touchstart", onTouchStart, { passive: true });
    scroller.addEventListener("touchmove", onTouchMove, { passive: true });
    scroller.addEventListener("touchend", onTouchEnd, { passive: true });
    scroller.addEventListener("touchcancel", onTouchEnd, { passive: true });
    scroller.addEventListener("pointerdown", onPointerDown, { passive: true });
    window.addEventListener("pointerup", finishScrollbarPointer, { passive: true });
    window.addEventListener("pointercancel", finishScrollbarPointer, { passive: true });
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener(PANEL_LAYOUT_COMMIT_EVENT, onPanelLayoutCommit);
    window.addEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);

    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (followingRef.current || forceBottomRef.current) {
            scheduleBottomSync(scroller);
          } else {
            // Streaming can move the bottom without a scroll event while the
            // user is reading history. Keep the return-to-latest affordance in
            // sync without re-enabling follow.
            setJumpVisible(!isNearBottom(scroller));
          }
        });

    observer?.observe(scroller);
    observer?.observe(content);

    return () => {
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("wheel", onWheel);
      scroller.removeEventListener("touchstart", onTouchStart);
      scroller.removeEventListener("touchmove", onTouchMove);
      scroller.removeEventListener("touchend", onTouchEnd);
      scroller.removeEventListener("touchcancel", onTouchEnd);
      scroller.removeEventListener("pointerdown", onPointerDown);
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
    forceBottomRef.current = false;
    returnIntentUntilRef.current = 0;
    setJumpVisible(false);

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      || document.documentElement.dataset.loomReducedMotion === "true";
    // Reuse the same owned follow loop instead of starting a second native
    // smooth-scroll animation that can race ResizeObserver during streaming.
    scheduleBottomSync(scroller, true, reducedMotion);
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

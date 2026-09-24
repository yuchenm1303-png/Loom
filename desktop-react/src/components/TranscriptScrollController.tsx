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
  const wasRunningRef = useRef(Boolean(running));
  const settleUntilRef = useRef(0);
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
    // User scroll intent must beat any queued streaming/layout follow frame.
    // Otherwise a pending rAF can pull the viewport back to the newest token
    // before Chromium dispatches the scroll event.
    followingRef.current = false;
    forceBottomRef.current = false;
    snapBottomRef.current = false;
    cancelScheduledScroll();
    setJumpVisible(!isNearBottom(scroller));
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
      const liveMotion = Boolean(running) || performance.now() < settleUntilRef.current;
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
    if (wasRunningRef.current && !nextRunning) {
      // Completion still changes geometry for a few hundred milliseconds while
      // the process stack folds and the final answer settles. Keep the same
      // bottom-follow spring alive through that handoff instead of switching to
      // an abrupt snap the instant runtime status flips to idle.
      settleUntilRef.current = performance.now() + LIVE_SETTLE_WINDOW_MS;
    }
    wasRunningRef.current = nextRunning;
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
      followingRef.current = true;
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
      followingRef.current = true;
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

    followingRef.current = true;
    forceBottomRef.current = true;
    lastScrollTopRef.current = scroller.scrollTop;
    setJumpVisible(false);
    // Mount/thread swaps are authoritative navigation and should not visibly
    // travel from whatever scroll position belonged to the previous subtree.
    scheduleBottomSync(scroller, true, true);

    const onScroll = () => {
      if (isPanelResizeActive()) return;

      const nextScrollTop = scroller.scrollTop;
      const nearBottom = isNearBottom(scroller);
      const movedUp = nextScrollTop < lastScrollTopRef.current - SCROLL_EPSILON_PX;
      const movedDown = nextScrollTop > lastScrollTopRef.current + SCROLL_EPSILON_PX;

      // Upward movement wins even inside the near-bottom threshold. Previously
      // the nearBottom branch re-enabled follow first, so small upward wheel
      // gestures were immediately overwritten by the next streaming frame.
      if (movedUp && !forceBottomRef.current) {
        followingRef.current = false;
      } else if (nearBottom && (followingRef.current || movedDown)) {
        followingRef.current = true;
      }

      lastScrollTopRef.current = nextScrollTop;
      setJumpVisible(!nearBottom && !followingRef.current);
    };

    const onWheel = (event: WheelEvent) => {
      if (isPanelResizeActive()) return;
      if (event.deltaY < 0) detachFromLiveFollow(scroller);
    };

    const onTouchStart = (event: TouchEvent) => {
      touchYRef.current = event.touches[0]?.clientY ?? null;
    };

    const onTouchMove = (event: TouchEvent) => {
      if (isPanelResizeActive()) return;
      const nextY = event.touches[0]?.clientY;
      const previousY = touchYRef.current;
      if (nextY == null) return;
      if (previousY != null && nextY > previousY + SCROLL_EPSILON_PX) {
        detachFromLiveFollow(scroller);
      }
      touchYRef.current = nextY;
    };

    const onTouchEnd = () => {
      touchYRef.current = null;
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
      window.removeEventListener(PANEL_LAYOUT_COMMIT_EVENT, onPanelLayoutCommit);
      window.removeEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);
      observer?.disconnect();
      cancelScheduledScroll();
    };
  }, [threadId]);

  const jumpToLatest = () => {
    const scroller = transcriptScroller();
    if (!scroller) return;

    followingRef.current = true;
    forceBottomRef.current = false;
    setJumpVisible(false);

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: reducedMotion ? "auto" : "smooth",
    });
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

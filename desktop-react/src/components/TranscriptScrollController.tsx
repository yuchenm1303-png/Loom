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
const LIVE_FOLLOW_MAX_DISTANCE_PX = 240;
const LIVE_FOLLOW_MAX_STEP_PX = 54;
const LIVE_FOLLOW_EASE = 0.38;
const PANEL_RESIZE_END_EVENT = "loom:panel-resize-end";

function transcriptScroller(): HTMLDivElement | null {
  return document.querySelector<HTMLDivElement>(".conversation-stage > .transcript-scroll");
}

function isPanelResizeActive(): boolean {
  return document.body.classList.contains("loom-panel-resizing");
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
  const lastScrollTopRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const [jumpVisible, setJumpVisible] = useState(false);
  const latestUserId = useMemo(() => latestUserMessageId(items), [items]);

  const cancelScheduledScroll = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  };

  const scheduleBottomSync = (scroller: HTMLDivElement, force = false) => {
    if (force) forceBottomRef.current = true;
    if (isPanelResizeActive() || frameRef.current !== null) return;

    const step = () => {
      frameRef.current = null;
      if (isPanelResizeActive()) return;

      const forced = forceBottomRef.current;
      forceBottomRef.current = false;
      if (!followingRef.current && !forced) return;

      const target = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const distance = target - scroller.scrollTop;
      const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
        || document.documentElement.dataset.loomReducedMotion === "true";
      const easeLiveGrowth = Boolean(
        running
        && !forced
        && !reducedMotion
        && distance > SCROLL_EPSILON_PX
        && distance <= LIVE_FOLLOW_MAX_DISTANCE_PX
      );

      if (easeLiveGrowth) {
        const delta = Math.min(
          LIVE_FOLLOW_MAX_STEP_PX,
          Math.max(2, distance * LIVE_FOLLOW_EASE),
        );
        scroller.scrollTop = Math.min(target, scroller.scrollTop + delta);
      } else {
        scroller.scrollTop = target;
      }

      lastScrollTopRef.current = scroller.scrollTop;
      followingRef.current = true;
      setJumpVisible(false);

      if (easeLiveGrowth && target - scroller.scrollTop > SCROLL_EPSILON_PX) {
        frameRef.current = requestAnimationFrame(step);
      }
    };

    frameRef.current = requestAnimationFrame(step);
  };

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    if (!scroller) return;

    const nextThreadId = String(threadId ?? "");
    const nextTurnId = String(currentTurnId ?? "");
    const threadChanged = nextThreadId !== lastThreadIdRef.current;
    const turnChanged = Boolean(nextTurnId) && nextTurnId !== lastTurnIdRef.current;
    const userMessageAdded = Boolean(latestUserId) && latestUserId !== lastUserMessageIdRef.current;

    if (threadChanged || turnChanged || userMessageAdded) {
      // A conversation/turn transition is an explicit request to work at the
      // newest message. Mark this as forced so the native scroll event caused
      // by replacing the old transcript cannot cancel the pending bottom sync.
      followingRef.current = true;
      setJumpVisible(false);
      scheduleBottomSync(scroller, true);
    } else if (followingRef.current) {
      scheduleBottomSync(scroller);
    }

    lastThreadIdRef.current = nextThreadId;
    lastTurnIdRef.current = nextTurnId;
    lastUserMessageIdRef.current = latestUserId;
  }, [items, latestUserId, threadId, currentTurnId, running]);

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    const content = scroller?.querySelector<HTMLElement>(".transcript");
    if (!scroller || !content) return;

    followingRef.current = true;
    forceBottomRef.current = true;
    lastScrollTopRef.current = scroller.scrollTop;
    setJumpVisible(false);
    scheduleBottomSync(scroller, true);

    const onScroll = () => {
      if (isPanelResizeActive()) return;

      const nextScrollTop = scroller.scrollTop;
      const nearBottom = isNearBottom(scroller);
      const movedUp = nextScrollTop < lastScrollTopRef.current - SCROLL_EPSILON_PX;

      // Content growth can make the viewport temporarily far from the bottom
      // without changing scrollTop. Only an actual upward viewport movement is
      // allowed to leave follow mode. forceBottomRef protects thread swaps from
      // the browser clamping the old scrollTop while the new transcript mounts.
      if (nearBottom) {
        followingRef.current = true;
      } else if (movedUp && !forceBottomRef.current) {
        followingRef.current = false;
      }

      lastScrollTopRef.current = nextScrollTop;
      setJumpVisible(!nearBottom && !followingRef.current);
    };

    const onPanelResizeEnd = () => {
      if (followingRef.current || forceBottomRef.current) scheduleBottomSync(scroller);
    };

    scroller.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);

    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (followingRef.current || forceBottomRef.current) scheduleBottomSync(scroller);
        });

    observer?.observe(scroller);
    observer?.observe(content);

    return () => {
      scroller.removeEventListener("scroll", onScroll);
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
      behavior: running || reducedMotion ? "auto" : "smooth",
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

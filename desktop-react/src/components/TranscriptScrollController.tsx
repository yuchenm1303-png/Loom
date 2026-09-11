import { useLayoutEffect, useMemo, useRef } from "react";
import type { TranscriptItem } from "../types/loom";
import "./transcript-scroll-stability.css";

interface TranscriptScrollControllerProps {
  items: TranscriptItem[];
  threadId?: string | null;
  currentTurnId?: string | null;
  running?: boolean;
}

const BOTTOM_THRESHOLD_PX = 96;

function transcriptScroller(): HTMLDivElement | null {
  return document.querySelector<HTMLDivElement>(".conversation-stage > .transcript-scroll");
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
 * - Opening a thread starts at its newest message.
 * - Sending a new turn returns to the newest message.
 * - Streaming deltas stay pinned only while the user is already near bottom.
 * - Scrolling upward deliberately suspends auto-follow until the user returns.
 * - Content/viewport resizes (markdown reflow, progress strip, tool expansion)
 *   preserve the bottom lock instead of making the transcript jump.
 */
export function TranscriptScrollController({
  items,
  threadId,
  currentTurnId,
  running,
}: TranscriptScrollControllerProps) {
  const stickToBottomRef = useRef(true);
  const lastThreadIdRef = useRef(String(threadId ?? ""));
  const lastTurnIdRef = useRef(String(currentTurnId ?? ""));
  const lastUserMessageIdRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const latestUserId = useMemo(() => latestUserMessageId(items), [items]);

  const cancelScheduledScroll = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  };

  const scheduleBottomSync = (scroller: HTMLDivElement) => {
    cancelScheduledScroll();
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      if (!stickToBottomRef.current) return;
      scroller.scrollTop = scroller.scrollHeight;
    });
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
      stickToBottomRef.current = true;
    }

    lastThreadIdRef.current = nextThreadId;
    lastTurnIdRef.current = nextTurnId;
    lastUserMessageIdRef.current = latestUserId;

    if (stickToBottomRef.current) {
      // Layout effect keeps the current frame stable; the rAF catches markdown
      // and font layout that settles immediately after React commits.
      scroller.scrollTop = scroller.scrollHeight;
      scheduleBottomSync(scroller);
    }

    return cancelScheduledScroll;
  }, [items, latestUserId, threadId, currentTurnId, running]);

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    const content = scroller?.querySelector<HTMLElement>(".transcript");
    if (!scroller || !content) return;

    stickToBottomRef.current = true;
    scroller.scrollTop = scroller.scrollHeight;

    const onScroll = () => {
      stickToBottomRef.current = isNearBottom(scroller);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });

    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (stickToBottomRef.current) scheduleBottomSync(scroller);
        });
    observer?.observe(scroller);
    observer?.observe(content);

    scheduleBottomSync(scroller);

    return () => {
      scroller.removeEventListener("scroll", onScroll);
      observer?.disconnect();
      cancelScheduledScroll();
    };
  }, [threadId]);

  return null;
}

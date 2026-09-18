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
 * Important performance rule: item updates never synchronously read scrollHeight.
 * Tool/process events often arrive in small bursts, and forcing layout directly
 * after every React commit made the newly inserted task-flow row feel like a UI
 * hitch. All follow-to-bottom work is now coalesced to at most one rAF callback
 * per frame; ResizeObserver and transcript updates share that same callback.
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
  const forceBottomRef = useRef(false);
  const forceSettleFrameRef = useRef<number | null>(null);
  const latestUserId = useMemo(() => latestUserMessageId(items), [items]);

  const cancelScheduledScroll = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    if (forceSettleFrameRef.current !== null) {
      cancelAnimationFrame(forceSettleFrameRef.current);
      forceSettleFrameRef.current = null;
    }
  };

  const scheduleBottomSync = (scroller: HTMLDivElement) => {
    if (isPanelResizeActive() || frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      if (!stickToBottomRef.current || isPanelResizeActive()) return;
      scroller.scrollTop = scroller.scrollHeight;
    });
  };

  const forceBottomAfterThreadSwitch = (scroller: HTMLDivElement) => {
    cancelScheduledScroll();
    forceBottomRef.current = true;
    stickToBottomRef.current = true;

    // The thread's DOM has already committed by layout-effect time, so jump
    // once immediately, then re-assert the bottom for two paint frames. Browser
    // scroll anchoring/content clamping can emit a passive scroll while the new
    // transcript replaces the old one; that event must not be mistaken for a
    // user intentionally scrolling up.
    scroller.scrollTop = scroller.scrollHeight;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      scroller.scrollTop = scroller.scrollHeight;
      forceSettleFrameRef.current = requestAnimationFrame(() => {
        forceSettleFrameRef.current = null;
        scroller.scrollTop = scroller.scrollHeight;
        forceBottomRef.current = false;
        stickToBottomRef.current = true;
      });
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

    // Thread identity changes are handled by the dedicated effect below. It
    // keeps a short force-bottom window so DOM replacement scroll events cannot
    // cancel the initial jump before the new transcript settles.
    if (!threadChanged && stickToBottomRef.current) scheduleBottomSync(scroller);
    // Deliberately no per-update cleanup here. Rapid item commits should share
    // the already queued frame instead of repeatedly cancelling and restarting it.
  }, [items, latestUserId, threadId, currentTurnId, running]);

  useLayoutEffect(() => {
    const scroller = transcriptScroller();
    const content = scroller?.querySelector<HTMLElement>(".transcript");
    if (!scroller || !content) return;

    forceBottomAfterThreadSwitch(scroller);

    const onScroll = () => {
      if (isPanelResizeActive() || forceBottomRef.current) return;
      stickToBottomRef.current = isNearBottom(scroller);
    };
    const onPanelResizeEnd = () => {
      if (stickToBottomRef.current) scheduleBottomSync(scroller);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);

    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (forceBottomRef.current) {
            scroller.scrollTop = scroller.scrollHeight;
            return;
          }
          if (stickToBottomRef.current) scheduleBottomSync(scroller);
        });
    observer?.observe(scroller);
    observer?.observe(content);

    return () => {
      scroller.removeEventListener("scroll", onScroll);
      window.removeEventListener(PANEL_RESIZE_END_EVENT, onPanelResizeEnd);
      observer?.disconnect();
      forceBottomRef.current = false;
      cancelScheduledScroll();
    };
  }, [threadId]);

  return null;
}

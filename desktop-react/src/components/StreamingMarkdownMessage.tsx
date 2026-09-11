import { useEffect, useRef, useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";
import "./streaming-markdown.css";

interface StreamingMarkdownMessageProps {
  content: string;
  compact?: boolean;
  streaming?: boolean;
}

const FRAME_INTERVAL_MS = 28;
const MIN_STEP = 1;
const MAX_STEP = 18;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function nextSliceLength(backlog: number): number {
  if (backlog <= 3) return 1;
  if (backlog <= 12) return 2;
  if (backlog <= 32) return 4;
  if (backlog <= 72) return 7;
  if (backlog <= 160) return 11;
  return MAX_STEP;
}

/**
 * Presentation-only smoothing for provider streaming.
 *
 * Runtime state always receives the real item/delta immediately. This component
 * only eases the visible string toward that authoritative text so providers that
 * emit coarse chunks do not make whole phrases pop into the transcript at once.
 */
export function StreamingMarkdownMessage({
  content,
  compact = false,
  streaming = false,
}: StreamingMarkdownMessageProps) {
  const targetRef = useRef(content);
  const displayedRef = useRef(streaming && !prefersReducedMotion() ? "" : content);
  const timerRef = useRef<number | null>(null);
  const [displayed, setDisplayed] = useState(displayedRef.current);

  const cancelTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const commit = (value: string) => {
    displayedRef.current = value;
    setDisplayed(value);
  };

  const schedule = () => {
    if (timerRef.current !== null) return;
    timerRef.current = window.setTimeout(function tick() {
      timerRef.current = null;
      const target = targetRef.current;
      const current = displayedRef.current;

      if (current === target) return;

      // Resync/retry can replace rather than append text. Do not animate through
      // content that no longer belongs to the authoritative stream.
      if (!target.startsWith(current)) {
        commit(target);
        return;
      }

      const backlog = target.length - current.length;
      const step = Math.max(MIN_STEP, Math.min(MAX_STEP, nextSliceLength(backlog)));
      commit(target.slice(0, current.length + step));

      if (displayedRef.current !== targetRef.current) schedule();
    }, FRAME_INTERVAL_MS);
  };

  useEffect(() => {
    targetRef.current = content;

    if (!streaming || prefersReducedMotion()) {
      cancelTimer();
      commit(content);
      return;
    }

    const current = displayedRef.current;
    if (!content.startsWith(current)) commit(content);
    else schedule();

    return cancelTimer;
    // `streaming` intentionally participates: completion flushes any remaining
    // visual backlog immediately so the durable message never trails runtime.
  }, [content, streaming]);

  useEffect(() => cancelTimer, []);

  return (
    <div className={`streaming-markdown ${streaming ? "is-streaming" : ""}`}>
      <MarkdownMessage content={displayed} compact={compact} />
      {streaming ? <span className="streaming-markdown-pulse" aria-hidden="true" /> : null}
    </div>
  );
}

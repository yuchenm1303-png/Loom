import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { advanceStreamingText, healStreamingMarkdown, streamingFrameInterval } from "./streamingText";
import { useReducedMotion } from "../motion/useReducedMotion";
import { STREAM_FINISH_MS } from "../presentationTiming";

// Scoped to a mounted turn: moving its final answer must not lose paint progress.
// Leaving a thread discards this state, so reopening history never replays it.
type Snapshot = { visible: string };
const PresentationContext = createContext<Map<string, Snapshot> | null>(null);
const PendingPresentationContext = createContext<ReadonlySet<string>>(new Set());
const ReportPresentationContext = createContext<((key: string, pending: boolean) => void) | null>(null);

export function usePendingPresentations() {
  return useContext(PendingPresentationContext);
}


export function StreamingPresentation({ children }: { children: ReactNode }) {
  const [snapshots] = useState(() => new Map<string, Snapshot>());
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const report = useCallback((key: string, painting: boolean) => {
    setPending((previous) => {
      if (previous.has(key) === painting) return previous;
      const next = new Set(previous);
      if (painting) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  return (
    <PresentationContext.Provider value={snapshots}>
      <ReportPresentationContext.Provider value={report}>
        <PendingPresentationContext.Provider value={pending}>{children}</PendingPresentationContext.Provider>
      </ReportPresentationContext.Provider>
    </PresentationContext.Provider>
  );
}

export function useStreamingPresentation(content: string, streaming: boolean, messageKey?: string, interrupted = false) {
  const snapshots = useContext(PresentationContext);
  const reportPresentation = useContext(ReportPresentationContext);
  const [initial] = useState(() => {
    const saved = messageKey ? snapshots?.get(messageKey) : undefined;
    return saved && content.startsWith(saved.visible) ? saved.visible : streaming ? "" : content;
  });
  const [visible, setVisible] = useState(initial);
  const reduce = useReducedMotion();
  const visibleRef = useRef(initial);
  const targetRef = useRef(content);
  const receivingRef = useRef(streaming);
  const frameRef = useRef<number | null>(null);
  const lastPaintAtRef = useRef(0);
  const drainStartedAtRef = useRef<number | null>(null);
  const animate = useRef(streaming || initial !== content);
  const painting = !reduce && !interrupted && visible !== content;

  // Publish before paint so activity arriving in this same commit cannot flash
  // below unfinished prose. Notify only on backlog boundaries, not every tick.
  useLayoutEffect(() => {
    if (messageKey) reportPresentation?.(messageKey, painting);
  }, [messageKey, painting, reportPresentation]);
  useLayoutEffect(() => () => {
    if (messageKey) reportPresentation?.(messageKey, false);
  }, [messageKey, reportPresentation]);

  useLayoutEffect(() => {
    if (messageKey) snapshots?.set(messageKey, { visible });
  }, [visible, messageKey, snapshots]);

  useEffect(() => {
    // Background rAF is suspended by Chromium. Resume at runtime truth rather
    // than replaying an invisible backlog when the user returns.
    const syncVisibility = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      lastPaintAtRef.current = 0;
      visibleRef.current = targetRef.current;
      setVisible(targetRef.current);
    };
    document.addEventListener("visibilitychange", syncVisibility);
    return () => document.removeEventListener("visibilitychange", syncVisibility);
  }, []);

  useLayoutEffect(() => {
    targetRef.current = content;
    receivingRef.current = streaming;
    animate.current ||= streaming;
    if (streaming) drainStartedAtRef.current = null;
    else drainStartedAtRef.current ??= performance.now();

    const commit = (next: string) => {
      visibleRef.current = next;
      setVisible(next);
    };
    const cancel = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      lastPaintAtRef.current = 0;
    };

    if (reduce || interrupted || document.hidden || !animate.current || !content.startsWith(visibleRef.current)) {
      cancel();
      commit(content);
      return;
    }

    const tick = (now: number) => {
      frameRef.current = null;
      const target = targetRef.current;
      const current = visibleRef.current;
      if (current === target) {
        lastPaintAtRef.current = now;
        return;
      }

      const elapsed = lastPaintAtRef.current ? now - lastPaintAtRef.current : 32;
      // Finish before the process fold (460ms). Large final chunks must not
      // hold tool entrances or keep the final answer typing for many seconds.
      if ((!receivingRef.current && drainStartedAtRef.current !== null
        && now - drainStartedAtRef.current >= STREAM_FINISH_MS) || elapsed > 1000) {
        commit(target);
        lastPaintAtRef.current = now;
        return;
      }
      if (lastPaintAtRef.current && elapsed < streamingFrameInterval(target.length)) {
        frameRef.current = requestAnimationFrame(tick);
        return;
      }

      lastPaintAtRef.current = now;
      const remainingMs = drainStartedAtRef.current === null ? Infinity
        : STREAM_FINISH_MS - (now - drainStartedAtRef.current);
      const next = advanceStreamingText(current, target, elapsed, !receivingRef.current, remainingMs);
      commit(next);
      if (next !== targetRef.current) frameRef.current = requestAnimationFrame(tick);
    };

    if (frameRef.current === null && visibleRef.current !== content) {
      frameRef.current = requestAnimationFrame(tick);
    }
  }, [content, streaming, reduce, interrupted]);

  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
  }, []);

  const shown = reduce || interrupted ? content : visible;
  // Text still arriving or still painting may stop mid-syntax; heal its edge.
  const unsettled = !interrupted && (streaming || shown !== content);
  const healed = useMemo(() => (unsettled ? healStreamingMarkdown(shown) : shown), [shown, unsettled]);

  return {
    visible: healed,
    painting,
    // Already visible content must not reanimate when moved out of the process area.
    fadeFrom: initial.length,
  };
}

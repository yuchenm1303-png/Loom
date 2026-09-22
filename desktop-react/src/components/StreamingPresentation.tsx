import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { PRESENTATION_FRAME_MS } from "../presentationTiming";
import { advanceStreamingText } from "./streamingText";

// Scoped to a mounted turn: moving its final answer must not lose paint progress.
// Leaving a thread discards this state, so reopening history never replays it.
type Snapshot = { visible: string };
const PresentationContext = createContext<Map<string, Snapshot> | null>(null);


export function StreamingPresentation({ children }: { children: ReactNode }) {
  const [snapshots] = useState(() => new Map<string, Snapshot>());
  return <PresentationContext.Provider value={snapshots}>{children}</PresentationContext.Provider>;
}

function reducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    || document.documentElement.dataset.loomReducedMotion === "true";
}

export function useStreamingPresentation(content: string, streaming: boolean, messageKey?: string, interrupted = false) {
  const snapshots = useContext(PresentationContext);
  const [initial] = useState(() => {
    const saved = messageKey ? snapshots?.get(messageKey) : undefined;
    return saved && content.startsWith(saved.visible) ? saved.visible : streaming ? "" : content;
  });
  const [visible, setVisible] = useState(initial);
  const [reduce, setReduce] = useState(reducedMotion);
  const visibleRef = useRef(initial);
  const targetRef = useRef(content);
  const receivingRef = useRef(streaming);
  const frameRef = useRef<number | null>(null);
  const lastPaintAtRef = useRef(0);
  const animate = useRef(streaming || initial !== content);

  useLayoutEffect(() => {
    if (messageKey) snapshots?.set(messageKey, { visible });
  }, [visible, messageKey, snapshots]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduce(Boolean(reducedMotion()));
    media.addEventListener("change", sync);
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-reduced-motion"] });
    return () => { media.removeEventListener("change", sync); observer.disconnect(); };
  }, []);

  useLayoutEffect(() => {
    targetRef.current = content;
    receivingRef.current = streaming;
    animate.current ||= streaming;

    const commit = (next: string) => {
      visibleRef.current = next;
      setVisible(next);
    };
    const cancel = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      lastPaintAtRef.current = 0;
    };

    if (reduce || interrupted || !animate.current || !content.startsWith(visibleRef.current)) {
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
      if (lastPaintAtRef.current && elapsed < PRESENTATION_FRAME_MS) {
        frameRef.current = requestAnimationFrame(tick);
        return;
      }

      lastPaintAtRef.current = now;
      const next = advanceStreamingText(current, target, elapsed, !receivingRef.current);
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

  return {
    visible: reduce || interrupted ? content : visible,
    painting: !reduce && !interrupted && visible !== content,
    // Already visible content must not reanimate when moved out of the process area.
    fadeFrom: initial.length,
  };
}

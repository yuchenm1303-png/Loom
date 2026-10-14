import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();
let disconnect: (() => void) | null = null;

// One query for every caller: matchMedia() allocates a new MediaQueryList each time, and this snapshot is read on
// every render of every message while a run streams.
let reducedQuery: MediaQueryList | null | undefined;
function reducedMotionQuery(): MediaQueryList | null {
  if (reducedQuery === undefined) {
    reducedQuery = typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null : null;
  }
  return reducedQuery;
}

function snapshot(): boolean {
  return typeof window !== "undefined" && (
    Boolean(reducedMotionQuery()?.matches)
    || document.documentElement.dataset.loomReducedMotion === "true"
  );
}

/** The preference right now, for code that runs outside a component (an event handler, a one-shot animation). */
export function prefersReducedMotionNow(): boolean {
  return snapshot();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!disconnect) {
    const notify = () => listeners.forEach((callback) => callback());
    const media = reducedMotionQuery();
    const observer = new MutationObserver(notify);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-reduced-motion"] });
    media?.addEventListener("change", notify);
    disconnect = () => {
      observer.disconnect();
      media?.removeEventListener("change", notify);
    };
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      disconnect?.();
      disconnect = null;
    }
  };
}

// History can mount hundreds of messages. Observe motion preferences once,
// rather than allocating one document observer per Markdown surface.
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}

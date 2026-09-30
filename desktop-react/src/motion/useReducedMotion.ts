import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();
let disconnect: (() => void) | null = null;

function snapshot(): boolean {
  return typeof window !== "undefined" && (
    Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches)
    || document.documentElement.dataset.loomReducedMotion === "true"
  );
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!disconnect) {
    const notify = () => listeners.forEach((callback) => callback());
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
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

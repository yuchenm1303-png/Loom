import { useSyncExternalStore } from "react";
import type { ProgressFeedbackMode } from "./types/loom";

export const DEFAULT_PROGRESS_FEEDBACK: ProgressFeedbackMode = "balanced";
const MODES: readonly ProgressFeedbackMode[] = ["quiet", "balanced", "detailed"];
const CHANGED_EVENT = "loom:progress-feedback-changed";
let current: ProgressFeedbackMode = DEFAULT_PROGRESS_FEEDBACK;
let loaded = false;

/** The Host owns the preference; Settings reports each value it loads or saves. */
export function applyProgressFeedback(mode: unknown): void {
  loaded = true;
  const next = MODES.includes(mode as ProgressFeedbackMode) ? (mode as ProgressFeedbackMode) : DEFAULT_PROGRESS_FEEDBACK;
  if (next === current) return;
  current = next;
  window.dispatchEvent(new Event(CHANGED_EVENT));
}

function load(): void {
  if (loaded) return;
  loaded = true;
  const call = (window as { loom?: { call?<T>(method: string, params: unknown): Promise<T> } }).loom?.call;
  if (!call) return;
  void call<{ settings?: { agent?: { progressFeedback?: unknown } } }>("settings/get", {})
    .then((result) => applyProgressFeedback(result?.settings?.agent?.progressFeedback))
    .catch(() => undefined);
}

function subscribe(onChange: () => void): () => void {
  load();
  window.addEventListener(CHANGED_EVENT, onChange);
  return () => window.removeEventListener(CHANGED_EVENT, onChange);
}

const snapshot = () => current;

export function useProgressFeedback(): ProgressFeedbackMode {
  return useSyncExternalStore(subscribe, snapshot, () => DEFAULT_PROGRESS_FEEDBACK);
}

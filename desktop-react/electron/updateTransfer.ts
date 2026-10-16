// Bound inactivity, not total duration: a slow connection that is still
// delivering bytes must not repeatedly discard a complete update range.
export function updateTransferDeadline(parent: AbortSignal, idleMs = 30000) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const abort = () => controller.abort(parent.reason);
  const progress = () => {
    if (timer) clearTimeout(timer);
    if (!controller.signal.aborted) {
      timer = setTimeout(() => controller.abort(new Error("Update transfer stalled")), idleMs);
      timer.unref?.();
    }
  };
  parent.addEventListener("abort", abort, { once: true });
  if (parent.aborted) abort();
  progress();
  return {
    signal: controller.signal,
    progress,
    dispose() {
      if (timer) clearTimeout(timer);
      parent.removeEventListener("abort", abort);
      controller.abort();
    },
  };
}

/** Serialize background reads and suspend them while the window is hidden. */
export function startVisiblePolling(read: () => Promise<unknown>, intervalMs: number): () => void {
  let disposed = false;
  let pending = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const visible = () => document.visibilityState !== "hidden";
  const run = async () => {
    if (disposed || pending || !visible()) return;
    clearTimeout(timer);
    pending = true;
    try { await read(); }
    catch { /* The caller owns presentation of read failures. */ }
    finally {
      pending = false;
      if (!disposed && visible()) timer = setTimeout(() => void run(), intervalMs);
    }
  };
  const visibility = () => {
    clearTimeout(timer);
    if (visible()) void run();
  };
  document.addEventListener("visibilitychange", visibility);
  void run();
  return () => {
    disposed = true;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visibility);
  };
}

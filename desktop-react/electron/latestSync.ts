/** Serialize updates, retaining requests arriving while an update is in flight. */
export function latestSync(update: () => Promise<void>): () => Promise<void> {
  let pending = false;
  let running: Promise<void> | null = null;
  return () => {
    pending = true;
    if (!running) {
      running = (async () => {
        // Start on a microtask so running is assigned even for synchronous errors.
        await Promise.resolve();
        try {
          while (pending) {
            pending = false;
            await update();
          }
        } finally {
          running = null;
        }
      })();
    }
    return running;
  };
}

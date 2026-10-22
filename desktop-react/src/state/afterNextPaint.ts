/** How long to wait for a frame that may never be drawn (a hidden or minimised window draws none). */
export const PAINT_WAIT_LIMIT_MS = 120;

/**
 * Resolves once the frame that follows the work being done now has been painted.
 *
 * An animation frame callback runs before the frame's style, layout and paint; a task queued from it runs after them.
 * Work that should not stand in front of the next thing the reader sees (answering a click, say) waits for this.
 */
export function afterNextPaint(): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    window.requestAnimationFrame(() => window.setTimeout(finish, 0));
    window.setTimeout(finish, PAINT_WAIT_LIMIT_MS);
  });
}

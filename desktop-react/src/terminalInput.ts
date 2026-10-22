/** Preserve byte order while coalescing input arriving during an IPC round trip.
 * A slow acknowledgement costs one batch, rather than one round trip per key.
 * Never retry failed stdin writes: commands may already have been executed.
 */
export function createTerminalInputPump(options: {
  send(text: string): Promise<unknown>;
  writable(): boolean;
  activity(): void;
  error(error: unknown): void;
}) {
  let pending = "";
  let sending = false;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const flush = async () => {
    timer = undefined;
    if (disposed || sending) return;
    if (!options.writable()) {
      pending = "";
      return;
    }
    if (!pending) return;
    sending = true;
    let length = Math.min(pending.length, 32768);
    const last = pending.charCodeAt(length - 1);
    if (length < pending.length && last >= 0xd800 && last <= 0xdbff)
      length -= 1;
    const text = pending.slice(0, length);
    pending = pending.slice(length);
    try {
      await options.send(text);
    } catch (error) {
      pending = "";
      if (!disposed && options.writable()) options.error(error);
    } finally {
      sending = false;
      if (!disposed) {
        options.activity();
        if (pending) void flush();
      }
    }
  };
  return {
    write(text: string) {
      if (disposed || !text || !options.writable()) return;
      pending += text;
      options.activity();
      if (!sending && timer === undefined)
        timer = setTimeout(() => void flush(), 4);
    },
    dispose() {
      disposed = true;
      pending = "";
      clearTimeout(timer);
    },
  };
}

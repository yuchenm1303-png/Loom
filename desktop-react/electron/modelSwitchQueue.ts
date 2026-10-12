/** Serialize writes per conversation, keeping unrelated conversations independent. */
export class ModelSwitchQueue {
  private pending = new Map<string, Promise<unknown>>();
  run<T>(key: string, action: () => Promise<T>): Promise<T> {
    const previous = this.pending.get(key) ?? Promise.resolve();
    const next = previous.catch(() => undefined).then(action);
    this.pending.set(key, next);
    void next.finally(() => { if (this.pending.get(key) === next) this.pending.delete(key); }).catch(() => undefined);
    return next;
  }
}

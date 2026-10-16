/** Serialize writes per conversation, keeping unrelated conversations independent. */
export interface ModelPreferenceTicket { isCurrent(): boolean }

export class ModelSwitchQueue {
  private pending = new Map<string, Promise<unknown>>();
  private preferenceVersion = 0;
  private preferenceWrite: Promise<unknown> = Promise.resolve();
  preference(): ModelPreferenceTicket {
    const version = ++this.preferenceVersion;
    return { isCurrent: () => version === this.preferenceVersion };
  }
  commitPreference(ticket: ModelPreferenceTicket, action: () => Promise<void>): Promise<void> {
    const next = this.preferenceWrite.catch(() => undefined).then(async () => {
      if (ticket.isCurrent()) await action();
    });
    this.preferenceWrite = next;
    return next;
  }
  run<T>(key: string, action: () => Promise<T>): Promise<T> {
    const previous = this.pending.get(key) ?? Promise.resolve();
    const next = previous.catch(() => undefined).then(action);
    this.pending.set(key, next);
    void next.finally(() => { if (this.pending.get(key) === next) this.pending.delete(key); }).catch(() => undefined);
    return next;
  }
}

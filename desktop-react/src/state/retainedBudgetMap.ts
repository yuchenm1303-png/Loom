/** Conservative retained-size estimate; never serialize/copy large transcripts. */
export function retainedBytes(value: unknown, ceiling = Infinity): number {
  const seen = new Set<object>();
  const pending: unknown[] = [value];
  let bytes = 0;
  while (pending.length && bytes <= ceiling) {
    const next = pending.pop();
    if (typeof next === "string") bytes += next.length * 2 + 24;
    else if (next && typeof next === "object" && !seen.has(next)) {
      seen.add(next);
      bytes += 64;
      for (const key of Object.keys(next)) {
        bytes += key.length * 2 + 16;
        pending.push(Reflect.get(next, key));
      }
    } else if (next !== null && next !== undefined && typeof next !== "object") bytes += 8;
  }
  return bytes;
}

/** LRU ordering is owned by callers; both count and retained bytes are bounded. */
export class RetainedBudgetMap<T> extends Map<string, T> {
  private weights = new Map<string, number>();
  private bytes = 0;
  constructor(private countLimit: number, private byteLimit: number, private entryLimit = byteLimit / 2) { super(); }

  override set(key: string, value: T): this {
    const weight = super.get(key) === value ? this.weights.get(key)! : retainedBytes(value, this.entryLimit);
    this.delete(key);
    // Oversized history can still be opened, but cannot evict useful small reads
    // or remain alive after the user switches to another conversation.
    if (weight > this.entryLimit) return this;
    super.set(key, value);
    this.weights.set(key, weight);
    this.bytes += weight;
    while (this.size > this.countLimit || this.bytes > this.byteLimit) {
      this.delete(this.keys().next().value!);
    }
    return this;
  }

  override delete(key: string): boolean {
    this.bytes -= this.weights.get(key) ?? 0;
    this.weights.delete(key);
    return super.delete(key);
  }

  override clear(): void {
    super.clear();
    this.weights.clear();
    this.bytes = 0;
  }
}

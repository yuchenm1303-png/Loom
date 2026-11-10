/**
 * When the Host's process ends without being asked to, start it again.
 *
 * Nothing did: the desktop only started the Host when a request needed it, and the scheduler that drives
 * a long goal lives in the Host. A Host that died at 3 a.m. left the goal stopped, and the phone showing
 * "Loom App Server is not running" over a stale "running", until someone opened the app. A Host that
 * keeps dying at once is a bug to look at, not to loop on, so the delay grows and the tries run out.
 */
import type { ChildProcess } from "node:child_process";

/** A failed spawn emits error + close, without exit. Observe both terminal events exactly once. */
export function observeHostExit(child: ChildProcess, ended: (code: number | null, signal: NodeJS.Signals | null) => void): void {
  const finish = (code: number | null, signal: NodeJS.Signals | null) => {
    child.removeListener("exit", finish);
    child.removeListener("close", finish);
    ended(code, signal);
  };
  child.once("exit", finish);
  child.once("close", finish);
}

export const RESPAWN_DELAYS_MS = [2_000, 5_000, 15_000, 30_000, 60_000, 120_000];
export const RESPAWN_MAX_TRIES = 8;
/** A Host that stayed up this long was healthy: its next death starts the count again. */
export const STABLE_AFTER_MS = 5 * 60_000;

export class RespawnPolicy {
  private failures = 0;
  private startedAt = 0;

  constructor(private readonly now: () => number = Date.now) {}

  started(): void {
    this.startedAt = this.now();
  }

  /** Milliseconds to wait before starting again after an unexpected exit; null when it has failed too often. */
  next(): number | null {
    if (this.startedAt && this.now() - this.startedAt >= STABLE_AFTER_MS) this.failures = 0;
    this.startedAt = 0;
    this.failures += 1;
    if (this.failures > RESPAWN_MAX_TRIES) return null;
    return RESPAWN_DELAYS_MS[Math.min(this.failures, RESPAWN_DELAYS_MS.length) - 1];
  }

  /** The person (or an update) started or stopped the Host on purpose: nothing to hold against it. */
  reset(): void {
    this.failures = 0;
    this.startedAt = 0;
  }
}

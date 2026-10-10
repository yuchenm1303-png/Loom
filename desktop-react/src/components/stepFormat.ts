/**
 * Small, dependency-free helpers for how one step of a task is described: how long it took, the last
 * thing a running command printed, and why a step failed. Presentation only; nothing here decides what
 * happened.
 */

export function parseTime(value: unknown): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** "1.2s", "34s", "2m 05s", "1h 02m". Under a second a step took no time worth reading. */
export function formatStepDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 1000) return "";
  if (ms < 10_000) return `${(Math.floor(ms / 100) / 10).toFixed(1)}s`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*\u0007/g;

/**
 * What a terminal would show for one line: control codes gone, and a line a progress bar keeps rewriting
 * with carriage returns is what it ended as, not every frame of it glued together.
 */
function cleanLine(line: string): string {
  const shown = line.replace(ANSI, "").split("\r").map((part) => part.trim()).filter(Boolean).pop() ?? "";
  return shown.replace(/\s+/g, " ");
}

const clip = (line: string): string => (line.length > 240 ? `${line.slice(0, 239)}…` : line);

/** The last non-empty line of a stream, which is what a running command is saying right now. */
export function lastOutputLine(...streams: Array<string | undefined>): string {
  for (const stream of streams) {
    if (!stream) continue;
    const lines = stream.split("\n");
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const line = cleanLine(lines[index]);
      if (line) return clip(line);
    }
  }
  return "";
}

/** A header that only announces that the reason follows. */
const REASON_FOLLOWS = /^(Traceback \(most recent call last\):?|Stack trace:?|Call stack:?)$/i;

/**
 * The reason a failed step gives, from the first place that has one: its first meaningful line, or, when that
 * line only announces a traceback, the last line, which is where Python says what went wrong.
 */
export function firstFailureLine(...sources: Array<string | undefined>): string {
  for (const source of sources) {
    if (!source) continue;
    const lines = source.split("\n").map(cleanLine).filter(Boolean);
    if (!lines.length) continue;
    return clip(REASON_FOLLOWS.test(lines[0]) ? lines[lines.length - 1] : lines[0]);
  }
  return "";
}

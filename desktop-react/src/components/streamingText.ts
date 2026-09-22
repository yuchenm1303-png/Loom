const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

const SENTENCE_BREAK = /[。！？!?；;\n]/u;
const SOFT_BREAK = /[，,：:\s]/u;

function paintBudget(remaining: number, elapsedMs: number, finalizing: boolean): number {
  const elapsed = Math.min(96, Math.max(12, Number.isFinite(elapsedMs) ? elapsedMs : 32));
  const backlogBoost = remaining > 1200
    ? 5
    : remaining > 480
      ? 3.4
      : remaining > 180
        ? 2.2
        : remaining > 72
          ? 1.5
          : 1;
  const baseRate = finalizing ? 112 : 66;
  const budget = Math.ceil((baseRate * backlogBoost * elapsed) / 1000);
  const catchupFloor = remaining > 420 ? 4 : remaining > 160 ? 2 : 1;
  return Math.min(finalizing ? 28 : 18, Math.max(catchupFloor, budget));
}

/**
 * Advance the presentation by a time-based grapheme budget.
 *
 * Provider chunks may arrive one token at a time or in large bursts. The UI
 * should not inherit that transport cadence, so the visual budget is based on
 * elapsed presentation time and backlog pressure instead. Sentence/word
 * boundaries are preferred once enough progress was made in this paint.
 */
export function advanceStreamingText(
  current: string,
  target: string,
  elapsedMs = 32,
  finalizing = false,
): string {
  if (!target.startsWith(current)) return target;
  if (current === target) return target;

  const remaining = target.length - current.length;
  const budget = paintBudget(remaining, elapsedMs, finalizing);
  const softBoundaryFloor = Math.max(3, Math.floor(budget * 0.72));

  let end = current.length;
  let count = 0;
  for (const part of segmenter.segment(target.slice(end))) {
    end += part.segment.length;
    count += 1;
    if (count >= budget) break;
    if (count >= 2 && SENTENCE_BREAK.test(part.segment)) break;
    if (count >= softBoundaryFloor && SOFT_BREAK.test(part.segment)) break;
  }
  return target.slice(0, end);
}

export function streamingGraphemes(value: string): string[] {
  return Array.from(segmenter.segment(value), (part) => part.segment);
}

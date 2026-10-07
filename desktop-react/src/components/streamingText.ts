const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

const SENTENCE_BREAK = /[。！？!?；;\n]/u;
const SOFT_BREAK = /[，,：:\s]/u;

// Parsing a growing Markdown table is substantially more expensive than prose.
// Batch long messages without dropping canonical text or slowing catch-up.
export function streamingFrameInterval(length: number): number {
  return length >= 12000 ? 120 : length >= 4000 ? 80 : 28;
}

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
  finishWithinMs = Infinity,
): string {
  if (!target.startsWith(current)) return target;
  if (current === target) return target;
  if (finalizing && finishWithinMs <= elapsedMs) return target;

  const remaining = target.length - current.length;
  const batches = streamingFrameInterval(target.length) > 28
    ? Math.max(1, Math.min(4, Math.floor(elapsedMs / 28)))
    : 1;
  const normalBudget = paintBudget(remaining, elapsedMs / batches, finalizing) * batches;
  // Spread a final provider burst over the remaining handoff window instead
  // of revealing a few glyphs and dumping the entire backlog at its deadline.
  const deadlineBudget = finalizing && Number.isFinite(finishWithinMs)
    ? Math.ceil(remaining * Math.min(1, Math.max(12, elapsedMs) / Math.max(1, finishWithinMs)))
    : 0;
  const budget = Math.max(normalBudget, deadlineBudget);
  const softBoundaryFloor = Math.max(3, Math.floor(budget * 0.72));

  let end = current.length;
  let count = 0;
  for (const part of segmenter.segment(target.slice(end))) {
    end += part.segment.length;
    count += 1;
    if (count >= budget) break;
    if (count >= Math.max(2 * batches, deadlineBudget) && SENTENCE_BREAK.test(part.segment)) break;
    if (count >= softBoundaryFloor && SOFT_BREAK.test(part.segment)) break;
  }
  return target.slice(0, end);
}

export function streamingGraphemes(value: string): string[] {
  return Array.from(segmenter.segment(value), (part) => part.segment);
}

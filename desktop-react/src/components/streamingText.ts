const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** Bound each paint, including coarse provider chunks; never split a grapheme. */
export function advanceStreamingText(current: string, target: string): string {
  if (!target.startsWith(current)) return target;
  const budget = Math.min(24, Math.max(1, Math.ceil((target.length - current.length) / 16)));
  let end = current.length;
  let count = 0;
  for (const part of segmenter.segment(target.slice(end))) {
    end += part.segment.length;
    if (++count >= budget) break;
  }
  return target.slice(0, end);
}

export function streamingGraphemes(value: string): string[] {
  return Array.from(segmenter.segment(value), (part) => part.segment);
}

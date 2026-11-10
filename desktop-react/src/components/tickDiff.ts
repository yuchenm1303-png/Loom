export interface Ghost { index: number; char: string }

/** What changed between two strings, matched from the right so a counter that gains a digit ("9s" -> "10s") keeps its
 *  unit still: the characters to swap (with the one that was there) and those that are new at their place. */
export function diffCharacters(previous: string, next: string): { ghosts: Ghost[]; fresh: number[]; whole?: string } {
  const before = Array.from(previous);
  const after = Array.from(next);
  const ghosts: Ghost[] = [];
  const fresh: number[] = [];
  for (let back = 0; back < after.length; back += 1) {
    const index = after.length - 1 - back;
    const old = before[before.length - 1 - back];
    if (old === undefined) fresh.push(index);
    else if (old !== after[index]) { ghosts.push({ index, char: old }); fresh.push(index); }
  }
  // A minute turning over ("3m 59s" -> "4m 0s") moves most of the text: that is one change, not four, so the old text
  // blurs out whole over the new, as a label does.
  if (ghosts.length > 2) return { ghosts: [], fresh: after.map((_, index) => index), whole: previous };
  return { ghosts, fresh };
}

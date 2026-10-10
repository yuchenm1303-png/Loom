/**
 * A live stage can run for dozens of steps (a browser session makes one per click). The reader follows
 * the newest ones, so only they stay as rows; the older ones fold into a single line at the top of the
 * stage that says what they were. This only decides how many fold, never what happened.
 */

/** How many of a stage's newest steps stay visible as rows while it is live. */
export const LIVE_ROW_WINDOW = 6;

/**
 * How many leading steps fold into the line. A line that replaces one row saves nothing, so nothing
 * folds until at least two would go.
 */
export function foldedCount(total: number, windowSize: number = LIVE_ROW_WINDOW): number {
  const folded = total - windowSize;
  return folded >= 2 ? folded : 0;
}

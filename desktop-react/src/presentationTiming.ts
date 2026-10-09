/**
 * Shared presentation cadence for live conversation UI.
 *
 * Runtime events remain immediate and lossless. These values only control when
 * the renderer reveals already-received state, so transport jitter does not
 * become visual jitter.
 */
export const PRESENTATION_FRAME_MS = 28;
/** A run-strip phase must stay readable at least this long before it changes. */
export const RUN_PHASE_MIN_DWELL_MS = 900;
/** Transitional phases ("analysing results") only show once the gap is real. */
export const RUN_PHASE_PRESENTATION_HOLD_MS = 280;
export const TURN_SETTLE_HOLD_MS = 460;
/** Completed process stack folding into its summary line (CSS: turn-flow.css). */
export const TURN_FOLD_MS = 380;
// Leave one presentation frame between the last glyph and process folding.
export const STREAM_FINISH_MS = TURN_SETTLE_HOLD_MS - 40;
/**
 * Quiet time between visible steps before the thinking capsule returns. Tool
 * chains routinely leave 100-300ms gaps; showing "thinking" in each of them
 * read as flicker.
 */
export const LIVE_STATUS_GRACE_MS = 420;
/**
 * A short sentence streaming after tool work is narration or the answer on its way, and the
 * runtime only says which when its response completes. It waits this long before it is drawn,
 * so quiet narration (whose tool call follows within a second or two) never flashes, and a
 * slow stream still shows up.
 */
export const LIVE_TEXT_HOLD_MS = 2000;
/** Run strip after the run ends: outcome readable for ~560ms, then a 320ms fade. */
export const RUN_STRIP_EXIT_MS = 900;
/** A thread read slower than this shows the switching indicator. */
export const THREAD_SWITCH_INDICATOR_DELAY_MS = 360;

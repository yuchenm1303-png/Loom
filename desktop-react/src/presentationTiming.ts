/**
 * Shared presentation cadence for live conversation UI.
 *
 * Runtime events remain immediate and lossless. These values only control when
 * the renderer reveals already-received state, so transport jitter does not
 * become visual jitter.
 */
export const PRESENTATION_FRAME_MS = 28;
export const RUN_PHASE_PRESENTATION_HOLD_MS = 130;
export const TURN_SETTLE_HOLD_MS = 460;

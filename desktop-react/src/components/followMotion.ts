/**
 * How the transcript's viewport travels to the live edge.
 *
 * Content arrives in steps: a line wraps (26px), a heading opens (49px), a code block lands (70px and more). Pinning
 * the viewport to the bottom in the same frame moves the whole page by that much at once, which reads as a stair
 * being climbed; pinning it a frame late (what a requestAnimationFrame scheduled from a ResizeObserver does) shows
 * the new text below the fold for a frame first and then jumps just the same.
 *
 * Instead the viewport follows with a critically damped spring. It closes the gap smoothly and never overshoots,
 * and because it carries its velocity when the target moves again, a long run of small steps reads as one even
 * drift while a single big step reads as one glide. The solution is closed-form, so any frame length integrates
 * exactly (a 6ms frame at 165Hz and a 40ms hitch land on the same curve).
 */

export type FollowProfile = "live" | "travel";

export interface FollowState {
  /** Where the viewport is, in CSS px of scrollTop. */
  position: number;
  /** Px per second; carried over when the target moves. */
  velocity: number;
}

/** Spring stiffness in rad/s. A gap closes to 5% in about 4.7/omega seconds: 180ms live, 360ms when travelling. */
const STIFFNESS: Record<FollowProfile, number> = { live: 26, travel: 13 };
/** Up to this gap the base stiffness applies; beyond it the spring stiffens so a big arrival is not left behind. */
const GAP_FREE_PX = 36;
/** Each of these beyond the free gap adds one more base stiffness (up to MAX_BOOST of them). */
const GAP_RAMP_PX = 90;
const MAX_BOOST = 5;
/** The viewport counts as arrived inside this distance at this speed. */
export const FOLLOW_SETTLE_PX = 0.4;
export const FOLLOW_SETTLE_SPEED = 12;
/** A frame longer than this is integrated as this long, so a hitch does not leap across the gap. */
export const FOLLOW_MAX_FRAME_S = 0.05;

/** Stiffer by this much while an animation is growing the page frame by frame. */
const FLOW_STIFFNESS_FACTOR = 3.5;
const FLOW_MOVE_PX = 0.5;
const FLOW_WINDOW_MS = 90;
const FLOW_MIN_MOVES = 3;

/**
 * Tells a page that is growing in single steps (a line wraps, a heading opens: glide to it) from one an animation
 * is growing frame after frame (a row unfolding over 300ms: stay with it). Gliding behind the second leaves the
 * new rows below the fold for the whole of their entrance, and the reader would not see them grow at all.
 * Only growth counts: a page shrinking (a fold) takes the view with it or lets it glide back, and is no reason to
 * stiffen the spring.
 */
export class TargetFlow {
  private last = Number.NaN;
  private moves: number[] = [];

  /** Record the target as measured at `now` (only when it was measured: an unchanged page moves nothing). */
  note(target: number, now: number): void {
    if (Number.isFinite(this.last) && target - this.last > FLOW_MOVE_PX) this.moves.push(now);
    this.last = target;
    this.drop(now);
  }

  flowing(now: number): boolean {
    this.drop(now);
    return this.moves.length >= FLOW_MIN_MOVES;
  }

  reset(): void {
    this.last = Number.NaN;
    this.moves = [];
  }

  private drop(now: number): void {
    while (this.moves.length && now - this.moves[0] > FLOW_WINDOW_MS) this.moves.shift();
  }
}

export function followStiffness(profile: FollowProfile, gap: number, flowing = false): number {
  const boost = Math.min(MAX_BOOST, Math.max(0, Math.abs(gap) - GAP_FREE_PX) / GAP_RAMP_PX);
  return STIFFNESS[profile] * (1 + boost) * (flowing ? FLOW_STIFFNESS_FACTOR : 1);
}

/** The integration step: the stiffness is re-read this often, so a frame's length does not change the curve. */
const SUBSTEP_S = 0.004;

/**
 * Advance the spring toward `target` by `seconds`.
 * With e = position - target and carry = velocity + w * e, a critically damped spring has
 *   e(t) = (e + carry * t) * exp(-w * t),   v(t) = (velocity - w * carry * t) * exp(-w * t)
 * and it overshoots only when carry points away from e, that is when the view is moving toward the target faster
 * than w * |e|. The stiffness follows the size of the gap, but is never allowed below velocity / |e|: velocity
 * gathered under a stiff spring carried into a soft one would run past the edge (a 700px arrival did, by 57px),
 * whereas a spring just stiff enough for the velocity it holds cannot.
 */
export function advanceFollow(
  state: FollowState,
  target: number,
  seconds: number,
  profile: FollowProfile,
  flowing = false,
): FollowState & { settled: boolean } {
  let remaining = Math.min(FOLLOW_MAX_FRAME_S, Math.max(0, Number.isFinite(seconds) ? seconds : 0));
  let { position, velocity } = state;
  while (remaining > 1e-9) {
    const dt = Math.min(SUBSTEP_S, remaining);
    remaining -= dt;
    const error = position - target;
    const toward = error === 0 ? 0 : -velocity * Math.sign(error);
    const w = Math.max(followStiffness(profile, error, flowing), toward > 0 ? toward / Math.abs(error) : 0);
    const decay = Math.exp(-w * dt);
    const carry = velocity + w * error;
    position = target + (error + carry * dt) * decay;
    velocity = (velocity - w * carry * dt) * decay;
    // Numerical edge: never past the target it was heading for.
    if ((position - target) * error < 0) {
      position = target;
      velocity = 0;
    }
  }
  if (Math.abs(position - target) < FOLLOW_SETTLE_PX && Math.abs(velocity) < FOLLOW_SETTLE_SPEED) {
    return { position: target, velocity: 0, settled: true };
  }
  return { position, velocity, settled: false };
}

/**
 * Scrolled text is drawn crisp only when the page sits on a whole device pixel; between two the glyphs are
 * resampled and shimmer as they drift. The spring keeps its fractional state; only what is written is snapped.
 */
export function snapScrollOffset(value: number, devicePixelRatio: number): number {
  const ratio = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  return Math.round(value * ratio) / ratio;
}

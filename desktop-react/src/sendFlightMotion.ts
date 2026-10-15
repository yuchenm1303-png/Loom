/**
 * The motion of the message capsule (sendFlight.ts), as numbers.
 *
 * A sent message leaves the field as a capsule: the text, a soft shell around it that has the composer's own material,
 * and the shadow that says it is above the page. This file is the physics of that trip and nothing else: it takes how
 * far the capsule goes and how big it is, and answers with keyframes (sampled every few milliseconds) for each part.
 * It reads nothing from the page and makes nothing; sendFlight.ts applies the answer with the Web Animations API, and
 * the tests run it on its own.
 *
 *   body    the text and its shell, together. It travels on two springs, one per axis, with different stiffness, so the
 *           path is an arc: it climbs out of the composer first and swings into its place on the right. It leaves at
 *           once (a spring starts from rest and is fast within a few frames: the text never lurches off the field, and
 *           never waits), is fastest early, arrives before the animation ends and overshoots by a few pixels, then
 *           comes back. The text only ever translates (and, once, meets the field's type size): glyphs are never
 *           squashed. The body leans a little into the climb, text and shell as one rigid thing.
 *   shell   the capsule around the text, and the part that is elastic. Its acceleration lengthens it a little (along
 *           its own length: stretching it along the way of travel would shear it against its text); touching down
 *           squashes it (flatter and wider) and it rebounds once and rests. Both are the response of one damped
 *           spring, so it moves like a body and not like a keyframe. On the way out it comes up with a small pop (it
 *           fills out around the text, mostly in height).
 *   halo    the shadow. It trails the capsule by an amount that follows its speed, is lifted from the page while the
 *           capsule is in the air, and is gone when it is down.
 *   ring    the light at touchdown: a glow that comes up round the bubble, swells a little and fades once.
 *
 * Every part ends exactly at rest (identity transform, hand-off state), and starts at rest: the first frame is the
 * field. Time inside is in seconds; what leaves is in milliseconds and offsets 0..1 of the whole.
 */

export interface MotionInput {
  /** Where the body starts, relative to where it rests: it travels from (dx, dy) to (0, 0). */
  dx: number;
  dy: number;
  /** The bubble's box. */
  width: number;
  height: number;
}

/** A frame of a WAAPI animation: the offset 0..1 and the properties set at it. */
export type Frame = { offset: number } & Record<string, string | number>;

export interface CapsuleMotion {
  /** The whole thing, ms. */
  duration: number;
  /** When the capsule first reaches its place, ms; the rest is the shell settling. */
  arrive: number;
  /** Distance travelled, px. */
  distance: number;
  /** translate of the body. */
  path: Frame[];
  /** rotate of the body (the lean). */
  lean: Frame[];
  /** transform (a scale, written as a matrix) of the shell: lengthening as it leaves, squash on touchdown. */
  shell: Frame[];
  /** scale of the skin (the pop it comes up with). */
  pop: Frame[];
  /**
   * opacity of the skin (the capsule's own material, as a pill), of the settle (the same material in the bubble's own
   * corners, which the pill gives way to on touchdown) and of the card (the bubble's quiet fill, which the material
   * finally gives way to).
   */
  skin: Frame[];
  settle: Frame[];
  card: Frame[];
  /** opacity, translate and scale of the halo. */
  halo: Frame[];
  /** opacity and scale of the ring. */
  ring: Frame[];
  /** Peak speed, px/s, and when it happens, ms (for the lab). */
  peak: { speed: number; at: number };
  /** Largest overshoot past the resting place, px, on each axis (signed the way the body was travelling). */
  overshoot: { x: number; y: number };
  /** Largest lengthening and squash (flattening) of the shell, as fractions. */
  strain: { stretch: number; squash: number };
}

export interface MotionTuning {
  /** First arrival time = arriveBase + arrivePerPx * distance (ms), clamped. */
  arriveBase: number;
  arrivePerPx: number;
  arriveMin: number;
  arriveMax: number;
  /** Duration = arrive * tail. */
  tail: number;
  /** The vertical axis arrives earlier and the horizontal later than the mean, which is what bends the path. */
  yRush: number;
  xDrag: number;
  /** The most the body may overshoot its place, px. */
  overshootCap: number;
  /** Largest stretch of the shell (fraction), reached on a long flight; shorter ones have less. */
  stretch: number;
  /** The pill lengthens by at most this many px in all (a wide capsule lengthens by a smaller fraction). */
  stretchLimitPx: number;
  /** Touchdown squash (fraction of the shell's height). */
  squash: number;
  /** The shell's own spring: natural frequency (Hz) and damping ratio. */
  shellHz: number;
  shellZeta: number;
  /** Lean into the climb, degrees at peak speed. */
  lean: number;
  /** Largest lag of the halo behind the capsule, px, and how far the capsule is lifted off the page, px. */
  haloTrail: number;
  haloLift: number;
  /** The skin fills out from this scale (x, y; 1 = no pop): it puffs up around the text, mostly in height. */
  popFrom: [number, number];
  /** The ring swells by this fraction while it fades. */
  ringSwell: number;
  /** Squash on touchdown is at most this many px, whatever the height of the shell. */
  squashLimitPx: number;
}

export const TUNING: MotionTuning = {
  arriveBase: 290,
  arrivePerPx: 0.2,
  arriveMin: 320,
  arriveMax: 470,
  tail: 1.4,
  yRush: 0.82,
  xDrag: 1.22,
  overshootCap: 5,
  stretch: 0.06,
  stretchLimitPx: 14,
  squash: 0.075,
  shellHz: 7,
  shellZeta: 0.5,
  lean: 1.5,
  haloTrail: 11,
  haloLift: 6,
  popFrom: [0.985, 0.9],
  ringSwell: 0.1,
  squashLimitPx: 4.5,
};

const SAMPLE_MS = 8;

/** The step response of a damped spring from 0 to 1 (t in seconds; w in rad/s; z the damping ratio, below 1). */
export function springStep(t: number, w: number, z: number): number {
  if (t <= 0) return 0;
  const zeta = Math.min(0.999, Math.max(0.05, z));
  const wd = w * Math.sqrt(1 - zeta * zeta);
  return 1 - Math.exp(-zeta * w * t) * (Math.cos(wd * t) + ((zeta * w) / wd) * Math.sin(wd * t));
}

/** Damping ratio at which a spring overshoots by `fraction` of its travel. */
function zetaForOvershoot(fraction: number): number {
  const f = Math.min(0.5, Math.max(1e-5, fraction));
  const l = -Math.log(f);
  return l / Math.sqrt(Math.PI * Math.PI + l * l);
}

/** A spring that reaches its target at `arrive` seconds for the first time, and the most it overshoots by `fraction`. */
function springFor(arrive: number, fraction: number): { w: number; z: number } {
  const z = Math.min(0.995, Math.max(0.78, zetaForOvershoot(fraction)));
  const arrival = (Math.PI - Math.atan(Math.sqrt(1 - z * z) / z)) / Math.sqrt(1 - z * z);
  return { w: arrival / arrive, z };
}

/** A spring's progress, brought to exactly 1 (with no speed) at the end: its last tenth of a second would otherwise stop short. */
function settled(progress: number, t: number, duration: number): number {
  const start = duration * 0.7;
  if (t <= start) return progress;
  const k = Math.min(1, (t - start) / (duration - start));
  const envelope = 0.5 + 0.5 * Math.cos(Math.PI * k);
  return 1 + (progress - 1) * envelope;
}

const smooth = (edge0: number, edge1: number, x: number): number => {
  const k = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return k * k * (3 - 2 * k);
};

const round = (value: number, digits = 3): number => {
  const k = 10 ** digits;
  return Math.round(value * k) / k;
};

/** A response of the shell's spring (ws rad/s, zeta) to a force over time, integrated every `dt` seconds. */
function respond(force: (i: number) => number, steps: number, dt: number, ws: number, zeta: number): number[] {
  const out: number[] = new Array(steps + 1).fill(0);
  let e = 0;
  let v = 0;
  for (let i = 0; i <= steps; i += 1) {
    v += (-2 * zeta * ws * v - ws * ws * e + ws * ws * force(i)) * dt;
    e += v * dt;
    out[i] = e;
  }
  return out;
}

export function capsuleMotion(input: MotionInput, tuning: MotionTuning = TUNING): CapsuleMotion {
  const { dx, dy, width, height } = input;
  const distance = Math.hypot(dx, dy);
  const arrive = Math.round(Math.min(tuning.arriveMax, Math.max(tuning.arriveMin, tuning.arriveBase + tuning.arrivePerPx * distance)));
  const duration = Math.round(arrive * tuning.tail);
  const total = duration / 1000;
  const touch = arrive / 1000;
  const ux = distance ? -dx / distance : 0;
  const uy = distance ? -dy / distance : -1;

  // The two axes, each on a spring of its own.
  const overshootFraction = (travel: number): number => (Math.abs(travel) > 1 ? tuning.overshootCap / Math.abs(travel) : 0.01);
  const sy = springFor(touch * tuning.yRush, Math.min(0.012, overshootFraction(dy)));
  const sx = springFor(touch * tuning.xDrag, Math.min(0.01, overshootFraction(dx)));
  const progressY = (t: number): number => settled(springStep(t, sy.w, sy.z), t, total);
  const progressX = (t: number): number => settled(springStep(t, sx.w, sx.z), t, total);
  const offsetX = (t: number): number => dx * (1 - progressX(t));
  const offsetY = (t: number): number => dy * (1 - progressY(t));

  // Fine-grained pass (0.25 ms) for the speed and acceleration along the way of travel, and the shell's responses.
  const dt = 0.00025;
  const steps = Math.ceil(total / dt);
  const along: number[] = new Array(steps + 1); // how far along the way of travel the body has got (px)
  for (let i = 0; i <= steps; i += 1) {
    const t = i * dt;
    along[i] = (offsetX(t) - dx) * ux + (offsetY(t) - dy) * uy;
  }
  const speed = (i: number): number => (along[Math.min(steps, i + 1)] - along[Math.max(0, i - 1)]) / (2 * dt);
  const accel = (i: number): number => (along[Math.min(steps, i + 1)] - 2 * along[i] + along[Math.max(0, i - 1)]) / (dt * dt);

  let peakSpeed = 0;
  let peakAt = 0;
  for (let i = 0; i <= steps; i += 1) {
    const v = speed(i);
    if (v > peakSpeed) { peakSpeed = v; peakAt = i * dt; }
  }

  // The shell. Stretch: its strain follows the body's acceleration through a damped spring of its own (accelerating
  // stretches it, braking compresses it a little). It is a lengthening of the pill along its own length (horizontal), the
  // way a wide capsule shows speed, and not along the way of travel: that would shear it against its text. Squash:
  // touching down is an impulse into the same kind of spring, along the height (flatter and wider), which rebounds once
  // and rests.
  const ws = 2 * Math.PI * tuning.shellHz;
  const stretchRaw = respond((i) => accel(i) / (peakSpeed / 0.1 || 1), steps, dt, ws, tuning.shellZeta);
  const pulseCentre = touch - 0.03;
  const squashRaw = respond((i) => {
    const k = (i * dt - pulseCentre) / 0.03;
    return Math.abs(k) < 1 ? 0.5 + 0.5 * Math.cos(Math.PI * k) : 0;
  }, steps, dt, ws, tuning.shellZeta);
  const maxOf = (values: number[]): number => values.reduce((peak, value) => Math.max(peak, value), 0);
  // A short hop deforms the shell less than a long flight does.
  const reach = Math.min(1, Math.sqrt(distance / 360));
  // The pill lengthens by at most this much (a fraction, and not more than a few pixels at each end); a flight that is
  // mostly vertical lengthens it less, since its length is not the way it is going.
  const sideways = 0.5 + 0.5 * Math.abs(ux);
  const stretchTop = Math.min(tuning.stretch, tuning.stretchLimitPx / Math.max(1, width));
  const stretchGain = maxOf(stretchRaw) > 1e-9 ? (stretchTop * reach * sideways) / maxOf(stretchRaw) : 0;
  const squashGain = maxOf(squashRaw) > 1e-9 ? (Math.min(tuning.squash, tuning.squashLimitPx / Math.max(1, height)) * Math.max(0.55, reach)) / maxOf(squashRaw) : 0;
  const stretchAt = (i: number): number => (stretchRaw[i] >= 0 ? stretchRaw[i] : stretchRaw[i] * 0.6) * stretchGain;
  // The shell is at rest at the end: its last milliseconds go to exactly nothing.
  const rest = (i: number): number => 1 - smooth(total * 0.9, total, i * dt);

  const frames = Math.round(duration / SAMPLE_MS);
  const offsets: number[] = [];
  for (let k = 0; k <= frames; k += 1) offsets.push(k / frames);
  const at = (offset: number): number => offset * total;
  const index = (offset: number): number => Math.min(steps, Math.round((offset * total) / dt));

  const path: Frame[] = offsets.map((offset) => {
    const t = at(offset);
    return { offset, translate: `${round(offsetX(t))}px ${round(offsetY(t))}px` };
  });

  // The shell's scale: the stretch e along its length with the area-keeping squeeze across it, and the squash q (height
  // down by q, width up by q/2). Written as a matrix, which is all a scale is.
  const shell: Frame[] = offsets.map((offset) => {
    const i = index(offset);
    const e = stretchAt(i) * rest(i);
    const q = squashRaw[i] * squashGain * rest(i);
    const a = (1 + e) * (1 + q / 2);
    const d = (1 / Math.sqrt(1 + e)) * (1 - q);
    return { offset, transform: `matrix(${round(a, 5)}, 0, 0, ${round(d, 5)}, 0, 0)` };
  });

  // The lean: into the climb, with the speed, and gone before it lands. A wide capsule swings its ends further for the
  // same angle, so it leans less; a flight that is nearly vertical has no sideways to lean into.
  const leanSign = ux >= 0 ? -1 : 1;
  const slender = Math.min(1, Math.max(0.45, 340 / Math.max(1, width))) * Math.min(1, Math.abs(ux) / 0.4);
  // It follows the speed through a critically damped spring of its own, so it banks into the climb instead of switching on.
  const banked = respond((i) => Math.max(0, speed(i) / peakSpeed), steps, dt, 2 * Math.PI * 4.5, 0.95);
  const bankedPeak = Math.max(1e-9, ...banked);
  const lean: Frame[] = offsets.map((offset) => {
    const k = banked[index(offset)] / bankedPeak;
    const settle = 1 - smooth(total * 0.7, total * 0.96, offset * total);
    return { offset, rotate: `${round(leanSign * tuning.lean * k * settle * reach * slender, 3)}deg` };
  });

  // The skin fills out around the text at once as a pill. As the capsule touches down it relaxes in two steps that
  // do not overlap: first its shape (the same material, in the bubble's own corners, fades in over the pill: the pill
  // is the smaller of the two, so it is covered, and nothing dips), then its material (the card, the bubble's quiet
  // fill, takes over from the skin).
  const ms = (value: number): number => value / duration;
  const arriveOffset = ms(arrive);
  // What happens after touchdown is laid out in shares of the time that is left, whatever that is.
  const after = (share: number): number => arriveOffset + ms((duration - arrive) * share);
  const skin: Frame[] = offsets.map((offset) => ({
    offset,
    opacity: round(smooth(0, ms(48), offset) * (1 - smooth(after(0.3), after(0.42), offset)), 4),
  }));
  const settle: Frame[] = offsets.map((offset) => ({
    offset,
    opacity: round(smooth(arriveOffset - ms(10), after(0.3), offset) * (1 - smooth(after(0.45), after(0.96), offset)), 4),
  }));
  const card: Frame[] = offsets.map((offset) => ({
    offset,
    opacity: round(smooth(after(0.3), after(0.88), offset), 4),
  }));
  const popW = (2 * Math.PI) / 0.17;
  const pop: Frame[] = offsets.map((offset) => ({
    offset,
    scale: tuning.popFrom.map((from) => round(from + (1 - from) * springStep(at(offset), popW, 0.5), 4)).join(" "),
  }));

  // The halo trails by an amount that follows the speed and is lifted while the capsule is in the air.
  const halo: Frame[] = offsets.map((offset) => {
    const i = index(offset);
    const lag = Math.max(0, speed(i) / peakSpeed) * tuning.haloTrail;
    const lift = smooth(0, ms(80), offset) * (1 - smooth(arriveOffset - ms(30), after(0.77), offset));
    return {
      offset,
      opacity: round(lift, 4),
      translate: `${round(-ux * lag * lift, 3)}px ${round(-uy * lag * lift + tuning.haloLift * lift, 3)}px`,
      scale: round(1 + 0.04 * lift, 4),
    };
  });

  // The ring: light that comes up as the capsule touches down, swelling a little as it fades.
  const ring: Frame[] = offsets.map((offset) => {
    const t = (offset * duration - (arrive - 20)) / 1000;
    const lit = t <= 0 ? 0 : Math.min(1, t / 0.035) * Math.exp(-Math.max(0, t - 0.035) / 0.085);
    const grow = t <= 0 ? 0 : 1 - Math.exp(-t / 0.11);
    return { offset, opacity: round(lit * (1 - smooth(0.9, 1, offset)), 4), scale: round(1 - tuning.ringSwell / 2 + tuning.ringSwell * grow, 4) };
  });

  let overshootX = 0;
  let overshootY = 0;
  for (let k = 0; k <= frames; k += 1) {
    const t = at(offsets[k]);
    overshootX = Math.max(overshootX, (dx > 0 ? -1 : 1) * offsetX(t));
    overshootY = Math.max(overshootY, (dy > 0 ? -1 : 1) * offsetY(t));
  }
  let stretchPeak = 0;
  let squashPeak = 0;
  for (let i = 0; i <= steps; i += 1) {
    stretchPeak = Math.max(stretchPeak, stretchAt(i));
    squashPeak = Math.max(squashPeak, squashRaw[i] * squashGain);
  }

  return {
    duration,
    arrive,
    distance,
    path,
    lean,
    shell,
    pop,
    skin,
    settle,
    card,
    halo,
    ring,
    peak: { speed: peakSpeed, at: peakAt * 1000 },
    overshoot: { x: overshootX, y: overshootY },
    strain: { stretch: stretchPeak, squash: squashPeak },
  };
}

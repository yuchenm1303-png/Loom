/**
 * The motion of the message capsule (sendFlight.ts), as numbers.
 *
 * A sent message leaves the field as a capsule: the text, a soft shell round it that is the bubble's own material
 * lifted, and the shadow that says it is above the page. This file is the motion of that trip and nothing else: it takes
 * how far the capsule goes and answers with keyframes (sampled every few milliseconds) for each part. It reads nothing
 * from the page and makes nothing; sendFlight.ts applies the answer with the Web Animations API, and the tests run it on
 * its own.
 *
 * The motion is Material's arc, which is also how a thing that rises against gravity moves: the capsule goes along a
 * quarter of an ellipse, shallow first and steep at the end. Rising into its place, it first travels along the field's
 * level (the horizontal leads) and is lifted into the transcript in the last stretch, arriving moving straight up (a
 * thing that comes down would do it the other way round: steeply first, shallow at the end). It is on one clock, the
 * standard curve (a slow start, the fastest stretch a third of the way in, a long soft landing), so what is seen is one
 * movement with one shape and one speed that rises and falls: two springs of different stiffness, one per axis, would
 * bend the path but flatten the speed into a plateau that reads as constant. The time grows with the distance (a longer
 * way in the same time is a faster, harsher movement), within what an action that is made many times a day can take.
 *
 * Nothing is squashed, stretched, scaled, tilted or made to bounce. The send is started by a key press, which carries no
 * momentum, and a thing that has none arrives without overshoot; Telegram's send animation, which is public, has none
 * of those either: its work is all in the curves and the shape of the container.
 *
 * The only other thing that moves with it is how high the capsule is above the page (`lift`, 0 on the page, 1 in the
 * air). It comes up as the capsule leaves and goes down as the capsule closes on its place, so it touches down as it
 * stops. Its shadow is that height, and the material it is made of (the lifted pill, with its hairline) gives way to the
 * bubble's own quiet fill as that height goes to nothing.
 *
 * Every part ends exactly at rest (hand-off state) and starts at rest: the first frame is the field. Time inside is in
 * seconds; what leaves is in milliseconds and offsets 0..1 of the whole.
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
  /** When the clock is at 97%, ms: the capsule has all but arrived, what is left is it coming to rest. */
  arrive: number;
  /** Distance travelled, px. */
  distance: number;
  /** translate of the body. */
  path: Frame[];
  /** opacity of the skin (the capsule's own material, as a pill) and of the card (the bubble's quiet fill that it gives way to). */
  skin: Frame[];
  card: Frame[];
  /** opacity, translate and scale of the halo (the shadow). */
  halo: Frame[];
  /** The height above the page at each frame (0 at rest, 1 in the air), for the tests and the lab. */
  lift: number[];
  /** Peak speed, px/s, and when it happens, ms (for the lab). */
  peak: { speed: number; at: number };
  /** How far the path leaves the straight line at most, px. */
  arc: number;
}

export interface MotionTuning {
  /** Time = base + perPx * distance (ms), clamped. */
  base: number;
  perPx: number;
  min: number;
  max: number;
  /** The one clock: a cubic bezier, as in CSS (Material's standard curve). */
  curve: [number, number, number, number];
  /**
   * A flight along (nearly) one axis does not arc: the share of the shorter side to the longer one below which it is a
   * straight line on the clock, and above which it is the whole quarter ellipse (a smooth blend in between).
   */
  straight: [number, number];
  /** The height comes up over this long (ms), and goes down as the clock goes from the first of these to the second (0 at the field, 1 at rest). */
  liftIn: number;
  setDown: [number, number];
  /** The skin comes up round the text in this time (ms), is whole until the clock is at `skinOut[0]`, and is gone at `skinOut[1]`; the card is in over `cardIn`. */
  skinIn: number;
  skinOut: [number, number];
  cardIn: [number, number];
  /** Largest lag of the shadow behind the capsule, px, and how far the capsule is lifted off the page, px. */
  haloTrail: number;
  haloLift: number;
}

export const TUNING: MotionTuning = {
  base: 310,
  perPx: 0.11,
  min: 330,
  max: 420,
  curve: [0.4, 0, 0.2, 1],
  straight: [0.02, 0.12],
  liftIn: 70,
  setDown: [0.5, 0.96],
  skinIn: 28,
  skinOut: [0.8, 0.985],
  cardIn: [0.74, 0.97],
  haloTrail: 9,
  haloLift: 7,
};

const SAMPLE_MS = 8;

/** A CSS cubic-bezier as a function of time (0..1) to progress. */
export function bezier(curve: [number, number, number, number]): (t: number) => number {
  const [x1, y1, x2, y2] = curve;
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const x = (s: number): number => ((ax * s + bx) * s + cx) * s;
  const y = (s: number): number => ((ay * s + by) * s + cy) * s;
  return (t) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    let low = 0;
    let high = 1;
    for (let round = 0; round < 40; round += 1) {
      const mid = (low + high) / 2;
      if (x(mid) < t) low = mid; else high = mid;
    }
    return y((low + high) / 2);
  };
}

const smooth = (edge0: number, edge1: number, x: number): number => {
  const k = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return k * k * (3 - 2 * k);
};

const round = (value: number, digits = 3): number => {
  const k = 10 ** digits;
  return Math.round(value * k) / k;
};

export function capsuleMotion(input: MotionInput, tuning: MotionTuning = TUNING): CapsuleMotion {
  const { dx, dy } = input;
  const distance = Math.hypot(dx, dy);
  const duration = Math.round(Math.min(tuning.max, Math.max(tuning.min, tuning.base + tuning.perPx * distance)));
  const ux = distance ? -dx / distance : 0;
  const uy = distance ? -dy / distance : -1;
  const progress = bezier(tuning.curve);

  // Where the body is, as an offset from its place, at a share t of the time: a quarter of an ellipse. Going up (it starts
  // below its place) the horizontal axis is the one that leads; going down, the vertical one. A flight along one axis is a
  // straight line instead (the ellipse of a line has the bad habit of starting slower than the clock does).
  const rising = dy >= 0;
  const sides = Math.min(Math.abs(dx), Math.abs(dy)) / Math.max(1, Math.abs(dx), Math.abs(dy));
  const bend = smooth(tuning.straight[0], tuning.straight[1], sides);
  const offsetAt = (t: number): [number, number] => {
    const q = progress(t);
    const quick = Math.sin((Math.PI / 2) * q); // 0 -> 1, fast first
    const slow = 1 - Math.cos((Math.PI / 2) * q); // 0 -> 1, slow first
    const [ax, ay] = rising ? [dx * (1 - quick), dy * (1 - slow)] : [dx * (1 - slow), dy * (1 - quick)];
    return [dx * (1 - q) + bend * (ax - dx * (1 - q)), dy * (1 - q) + bend * (ay - dy * (1 - q))];
  };

  // A fine pass (0.5 ms): where it is, how far on the clock it is (0 at the field, 1 at rest in its place), and how fast
  // and which way it goes.
  const dt = 0.0005;
  const steps = Math.ceil(duration / 1000 / dt);
  const where: Array<[number, number]> = new Array(steps + 1);
  const clock: number[] = new Array(steps + 1);
  let arc = 0;
  for (let i = 0; i <= steps; i += 1) {
    const t = Math.min(1, (i * dt * 1000) / duration);
    const point = offsetAt(t);
    where[i] = point;
    clock[i] = progress(t);
    // Its distance from the straight line between the two places.
    arc = Math.max(arc, Math.abs((point[0] - dx) * -uy + (point[1] - dy) * ux));
  }
  const velocity = (i: number): [number, number] => {
    const a = where[Math.max(0, i - 1)];
    const b = where[Math.min(steps, i + 1)];
    return [(b[0] - a[0]) / (2 * dt), (b[1] - a[1]) / (2 * dt)];
  };
  let peakSpeed = 0;
  let peakAt = 0;
  for (let i = 0; i <= steps; i += 1) {
    const speed = Math.hypot(...velocity(i));
    if (speed > peakSpeed) { peakSpeed = speed; peakAt = i * dt * 1000; }
  }
  let arriveIndex = steps;
  for (let i = 0; i <= steps; i += 1) if (clock[i] >= 0.97) { arriveIndex = i; break; }
  const arrive = Math.round(arriveIndex * dt * 1000);

  const frames = Math.round(duration / SAMPLE_MS);
  const offsets: number[] = [];
  for (let k = 0; k <= frames; k += 1) offsets.push(k / frames);
  const index = (offset: number): number => Math.min(steps, Math.round((offset * duration) / 1000 / dt));
  const ms = (offset: number): number => offset * duration;

  const path: Frame[] = offsets.map((offset) => {
    const point = where[index(offset)];
    return { offset, translate: `${round(point[0])}px ${round(point[1])}px` };
  });

  // The height: up as the capsule leaves (it is picked up at once), down as it closes on its place. All of what follows is
  // a function of the clock (the standard curve brings the capsule to rest, and so it also puts it down), so the capsule
  // is set down at the same point of its slowing whatever way it has come: not by how far it has gone along the straight
  // line, which for a flat arc is nearly all of it long before it is in its place.
  const lift: number[] = offsets.map((offset) => smooth(0, tuning.liftIn, ms(offset)) * (1 - smooth(tuning.setDown[0], tuning.setDown[1], clock[index(offset)])));
  // The material relaxes as the capsule slows to its place and not on a clock of its own; the card (a thin wash over the
  // pill) is in before the pill is gone, so nothing is ever hollow.
  const skin: Frame[] = offsets.map((offset) => ({
    offset,
    opacity: round(smooth(0, tuning.skinIn, ms(offset)) * (1 - smooth(tuning.skinOut[0], tuning.skinOut[1], clock[index(offset)])), 4),
  }));
  const card: Frame[] = offsets.map((offset) => ({
    offset,
    opacity: round(smooth(tuning.cardIn[0], tuning.cardIn[1], clock[index(offset)]), 4),
  }));
  // The shadow is the height: it trails the capsule by an amount that follows the speed (on the way it is really going),
  // and is lifted while the capsule is in the air.
  const halo: Frame[] = offsets.map((offset, k) => {
    const [vx, vy] = velocity(index(offset));
    const speed = Math.hypot(vx, vy);
    const trail = peakSpeed > 0 ? (speed / peakSpeed) * tuning.haloTrail : 0;
    const up = lift[k];
    return {
      offset,
      opacity: round(up, 4),
      translate: `${round(speed > 0 ? (-vx / speed) * trail * up : 0, 3)}px ${round((speed > 0 ? (-vy / speed) * trail * up : 0) + tuning.haloLift * up, 3)}px`,
      scale: round(1 + 0.05 * up, 4),
    };
  });

  return {
    duration,
    arrive,
    distance,
    path,
    skin,
    card,
    halo,
    lift: lift.map((value) => round(value, 4)),
    peak: { speed: peakSpeed, at: peakAt },
    arc,
  };
}

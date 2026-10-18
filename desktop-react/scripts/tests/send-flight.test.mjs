// Source contracts of the send flight (src/sendFlight.ts, sendFlightMotion.ts, components/send-flight.css). The
// behaviour is held by send-flight-browser.mjs (a real page); these are what a page cannot see: where things are
// wired, what may move, the motion of the capsule (run on its own, it reads nothing from a page), and what must
// never be switched off by a late stylesheet.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (file) => fs.readFileSync(new URL(`../../src/${file}`, import.meta.url), "utf8");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const flight = read("sendFlight.ts");
const css = strip(read("components/send-flight.css"));
const motionSource = read("sendFlightMotion.ts");
const compiled = ts.transpileModule(motionSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { capsuleMotion, bezier } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

/**
 * The flights a person makes. The first is the one the arc was chosen on (a one-line message in a short thread); then a
 * very short message from the home screen, the flat flight of a long conversation (the commonest), a hop, wrapped
 * messages (their bubble starts where the field's text does: nearly straight up), one that goes down, and one along
 * a single axis.
 */
const FLIGHTS = [
  { dx: -367, dy: 372, width: 487, height: 44 },
  { dx: -620, dy: 372, width: 190, height: 44 },
  { dx: -650, dy: 110, width: 190, height: 44 },
  { dx: -420, dy: 120, width: 380, height: 44 },
  { dx: -140, dy: 70, width: 200, height: 44 },
  { dx: -40, dy: 150, width: 480, height: 90 },
  { dx: -40, dy: 520, width: 480, height: 132 },
  { dx: 200, dy: -300, width: 330, height: 44 },
  { dx: -500, dy: 0, width: 300, height: 44 },
  { dx: -6, dy: 400, width: 560, height: 66 },
];
/** What the capsule is made of: one part for each thing that moves, and nothing else (a new effect has to be argued for here). */
const PARTS = ["path", "skin", "card", "halo"];
const numbers = (text) => String(text).match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g).map(Number);
/** How much of the way is on the second side compared with the first: 1 for a diagonal, 0 for a flight along one axis. */
const sides = ({ dx, dy }) => Math.min(Math.abs(dx), Math.abs(dy)) / Math.max(Math.abs(dx), Math.abs(dy), 1);
/** The flights that bend (both sides count) and the ones along one axis (they are straight). */
const BENDING = FLIGHTS.filter((input) => sides(input) >= 0.12);
const STRAIGHT = FLIGHTS.filter((input) => sides(input) <= 0.02);
/** The value of a part's opacity at a time (ms), the frames being spread evenly over the whole. */
const at = (motion, frames, ms) => frames[Math.min(frames.length - 1, Math.round((ms / motion.duration) * (frames.length - 1)))].opacity;
/** Speed of the body at each gap between two frames, px/s. */
const speeds = (motion) => {
  const points = motion.path.map((frame) => numbers(frame.translate));
  const frameMs = motion.duration / (motion.path.length - 1);
  return points.slice(1).map((point, index) => Math.hypot(point[0] - points[index][0], point[1] - points[index][1]) / frameMs * 1000);
};

test("the flight's stylesheet is the last of the cascade, so what holds the entry back cannot be outranked by a later rule", () => {
  const cascade = read("renderer-styles.ts").split(/\r?\n/).filter((line) => line.startsWith("import "));
  assert.equal(cascade.at(-1), 'import "./components/send-flight.css";');
});

test("everything the flight moves is a transform, a scale, a rotation or an opacity on the compositor", () => {
  const allowed = ["translate", "transform", "scale", "rotate", "opacity"];
  const keyframes = [...flight.matchAll(/\.animate\(\s*\[([\s\S]*?)\]\s*,/g)].map((match) => match[1]);
  assert.ok(keyframes.length >= 3, `found ${keyframes.length} inline animations`);
  for (const frames of keyframes) {
    const properties = new Set([...frames.matchAll(/\b(translate|transform|scale|opacity|rotate|left|top|width|height|margin\w*|padding\w*|clipPath|filter|borderRadius|boxShadow)\s*:/g)].map((match) => match[1]));
    for (const property of properties) assert.ok(allowed.includes(property), `${property} is animated in: ${frames.trim().slice(0, 80)}`);
  }
  // The capsule's own parts come from the motion module, which answers in frames: every property of every frame.
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    for (const part of PARTS) {
      assert.ok(motion[part].length > 20, `${part} has frames`);
      for (const frame of motion[part]) for (const property of Object.keys(frame)) assert.ok(property === "offset" || allowed.includes(property), `${part} animates ${property}`);
    }
  }
  // The only thing written every frame is the ghost's own box (where the message is aimed), and only while it flies.
  assert.match(flight, /ghost\.style\.left = `\$\{entryBox\.left \+ aim\.x\}px`;/);
  assert.match(flight, /ghost\.style\.top = `\$\{finalTop \+ aim\.y\}px`;/);
  const keyframeBlocks = [...css.matchAll(/@keyframes\s+([\w-]+)\s*\{([\s\S]*?)\n\}/g)];
  // The placeholder's. (The message's toolbar had an arrival here, too; it has none now: it is there when the pointer is.)
  assert.ok(keyframeBlocks.length >= 1);
  for (const [, name, body] of keyframeBlocks) assert.doesNotMatch(body, /\b(width|height|top|left|margin|padding|grid-template-rows|clip-path|filter|box-shadow)\s*:/, name);
});

test("one movement and nothing else: a path, a pill, a card and a shadow; never scaled, tilted, stretched or made to bounce", () => {
  const motion = capsuleMotion(FLIGHTS[0]);
  const made = Object.keys(motion).filter((key) => Array.isArray(motion[key]) && motion[key].length && typeof motion[key][0] === "object");
  assert.deepEqual(made.sort(), [...PARTS].sort(), "the parts the motion makes");
  // The DOM builds exactly those: a shell of shadow, skin and card, and the text.
  assert.match(flight, /shell\.append\(halo, skin, card\);/);
  for (const gone of ["send-flight-settle", "send-flight-ring", "send-flight-membrane"]) assert.ok(!(flight + css).includes(gone), `${gone} is not part of the flight`);
  const code = motionSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(code, /squash|stretch|\bring\b|\bsettle\b|spring|overshoot|rotate|\blean\b|\bpop\b|\bwobble\b/, "no second effect in the motion");
  assert.doesNotMatch(flight, /rotate/, "the capsule is never tilted");
  // The accent is a glint in the hairline, not a light: nothing of the capsule is a gradient of it.
  assert.doesNotMatch(css.match(/\.send-flight-halo\s*\{[^}]*\}/)?.[0] ?? "", /accent/, "the shadow is neutral");
});

test("the capsule starts as the field and ends as the bubble: the first frame is at rest where the text was, the last is at rest where the entry is", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    assert.deepEqual(numbers(motion.path[0].translate), [input.dx, input.dy], `${label}: the first frame is the field`);
    assert.deepEqual(numbers(motion.path.at(-1).translate).map(Math.abs), [0, 0], `${label}: the last frame is the entry`);
    for (const part of PARTS) {
      assert.equal(motion[part][0].offset, 0);
      assert.equal(motion[part].at(-1).offset, 1);
    }
    // What is on show: nothing of the capsule yet (it comes up in the frames that follow, on the text), and at the end the bubble's own fill alone.
    assert.deepEqual([motion.skin[0].opacity, motion.card[0].opacity, motion.halo[0].opacity], [0, 0, 0], label);
    assert.deepEqual([motion.skin.at(-1).opacity, motion.card.at(-1).opacity, motion.halo.at(-1).opacity], [0, 1, 0], label);
    assert.deepEqual([motion.lift[0], motion.lift.at(-1)], [0, 0], `${label}: it is on the page at both ends`);
  }
});

test("the way is Material's arc, a quarter of an ellipse: along the field's level first, then up into its place, and arriving going straight up", () => {
  assert.ok(BENDING.length >= 6 && STRAIGHT.length >= 2, "the flights cover both kinds");
  for (const input of BENDING) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const rising = input.dy >= 0;
    const points = motion.path.map((frame) => numbers(frame.translate));
    // In the shares of the way on each side the path is a quarter circle (a quarter ellipse, in px).
    for (const [x, y] of points) {
      const radius = rising ? (1 - x / input.dx) ** 2 + (y / input.dy) ** 2 : (x / input.dx) ** 2 + (1 - y / input.dy) ** 2;
      assert.ok(Math.abs(radius - 1) < 0.01, `${label}: (${x}, ${y}) is not on the ellipse (${radius.toFixed(3)})`);
    }
    // Going up, the horizontal leads: it is always further on than the vertical. (Going down, the vertical leads.)
    for (const [x, y] of points) {
      const sideways = 1 - x / input.dx;
      const upwards = 1 - y / input.dy;
      assert.ok(rising ? sideways >= upwards - 1e-3 : upwards >= sideways - 1e-3, `${label}: the ${rising ? "horizontal" : "vertical"} leads (${sideways.toFixed(3)} vs ${upwards.toFixed(3)})`);
    }
    // So the path bulges toward the corner it goes round: on that side of the straight line at every frame, and by an amount that is seen.
    const sideOf = ([x, y]) => -input.dx * (y - input.dy) + input.dy * (x - input.dx);
    const corner = sideOf(rising ? [0, input.dy] : [input.dx, 0]);
    for (const point of points) assert.ok(sideOf(point) * corner >= -1e-3, `${label}: (${point}) is on the wrong side of the straight line`);
    assert.ok(points.some((point) => Math.abs(sideOf(point)) / motion.distance > 0.04 * motion.distance), `${label}: the arc is seen`);
    assert.ok(motion.arc > 0.04 * motion.distance, `${label}: the arc is ${motion.arc.toFixed(0)} px of ${motion.distance.toFixed(0)}`);
    // It arrives going the way of the last side (up, for a flight that rises): the last stretch is vertical.
    for (const [x, y] of points) {
      const left = Math.hypot(x, y);
      if (left > 1 && left < 0.04 * motion.distance) assert.ok(rising ? Math.abs(y) >= Math.abs(x) : Math.abs(x) >= Math.abs(y), `${label}: the last stretch (${x}, ${y}) is along the last side`);
    }
  }
  // The diagonal the arc was chosen on bulges by the quarter circle's own amount: a fifth of the distance.
  const chosen = capsuleMotion(FLIGHTS[0]);
  assert.ok(chosen.arc > 0.17 * chosen.distance && chosen.arc < 0.24 * chosen.distance, `the arc of the chosen flight is ${(chosen.arc / chosen.distance * 100).toFixed(0)}% of its distance`);
  // A flight along one side does not arc: it is a straight line.
  for (const input of STRAIGHT) {
    const motion = capsuleMotion(input);
    const distance = Math.hypot(input.dx, input.dy);
    for (const frame of motion.path) {
      const [x, y] = numbers(frame.translate);
      const off = Math.abs(x * input.dy - y * input.dx) / distance;
      assert.ok(off < 0.6, `${JSON.stringify(input)}: (${x}, ${y}) is ${off.toFixed(2)} px off the straight line`);
    }
  }
});

test("one clock: it leaves from rest, is fastest about a third of the way in, spends its time slowing into its place, and lands at rest (no plateau of constant speed)", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const speed = speeds(motion);
    const peak = Math.max(...speed);
    const peakAt = speed.indexOf(peak);
    assert.ok(Math.abs(peak - motion.peak.speed) < 0.06 * motion.peak.speed + 30, `${label}: the stated peak speed (${motion.peak.speed.toFixed(0)}) is the real one (${peak.toFixed(0)})`);
    assert.ok(speed[0] < 0.06 * peak, `${label}: it leaves from rest (${(speed[0] / peak * 100).toFixed(1)}% of its top speed in the first frame)`);
    assert.ok(speed.at(-1) < 0.02 * peak, `${label}: it lands at rest`);
    const share = (peakAt + 0.5) / speed.length;
    assert.ok(share > 0.2 && share < 0.4, `${label}: fastest ${(share * 100).toFixed(0)}% of the way in time`);
    const fast = speed.filter((value) => value > 0.8 * peak).length / speed.length;
    assert.ok(fast < 0.2, `${label}: within 80% of its top speed for ${(fast * 100).toFixed(0)}% of the time: that is a plateau, and a plateau reads as constant speed`);
    // A bell: faster up to the peak, slower after it (a frame of rounding aside).
    for (let index = 1; index <= peakAt; index += 1) assert.ok(speed[index] >= speed[index - 1] * 0.94 - 8, `${label}: it slows down on the way up to its top speed (frame ${index})`);
    for (let index = peakAt + 1; index < speed.length; index += 1) assert.ok(speed[index] <= speed[index - 1] * 1.06 + 8, `${label}: it speeds up again on the way down (frame ${index})`);
    // It has all but arrived when it first reaches its place, and nothing goes past it: the way is one way, with no bounce.
    const points = motion.path.map((frame) => numbers(frame.translate));
    for (const axis of [0, 1]) {
      const start = Math.sign([input.dx, input.dy][axis]);
      for (let index = 0; index < points.length; index += 1) {
        assert.ok(points[index][axis] * start >= -0.01, `${label}: axis ${axis} goes past its place (${points[index][axis]})`);
        if (index) assert.ok(Math.abs(points[index][axis]) <= Math.abs(points[index - 1][axis]) + 0.01, `${label}: axis ${axis} turns back at frame ${index}`);
      }
    }
    assert.ok(motion.arrive < motion.duration && motion.arrive > 0.55 * motion.duration && motion.arrive < 0.85 * motion.duration, `${label}: it has all but arrived at ${motion.arrive} of ${motion.duration} ms`);
  }
});

test("the time grows with the distance, within what an action made many times a day can take, and no flight is so fast that it is a jump", () => {
  const along = (distance) => capsuleMotion({ dx: -distance, dy: distance / 3, width: 300, height: 44 }).duration;
  let last = 0;
  for (const distance of [30, 100, 200, 300, 450, 600, 800, 1100, 1600]) {
    const time = along(distance);
    assert.ok(time >= last, `${distance} px takes ${time} ms, less than a shorter one`);
    assert.ok(time >= 330 && time <= 420, `${distance} px takes ${time} ms`);
    last = time;
  }
  assert.ok(along(1600) > along(300) + 40, "a long way takes longer");
  const chosen = capsuleMotion(FLIGHTS[0]);
  assert.ok(chosen.duration >= 345 && chosen.duration <= 385, `the flight the arc was chosen on takes ${chosen.duration} ms (it was shown at 340)`);
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    assert.ok(motion.peak.speed < 7000, `${JSON.stringify(input)}: top speed ${motion.peak.speed.toFixed(0)} px/s`);
  }
});

test("the clock is Material's standard curve as CSS draws it (cubic-bezier(.4, 0, .2, 1)), whatever a cubic-bezier is asked for", () => {
  const standard = bezier([0.4, 0, 0.2, 1]);
  assert.equal(standard(0), 0);
  assert.equal(standard(1), 1);
  // Reference values, from sampling the curve's own parametric form finely and from Newton's method: both agree to 5 places.
  for (const [time, value] of [[0.1, 0.02586], [0.25, 0.23659], [0.5, 0.77556], [0.75, 0.95937], [0.9, 0.99435]]) assert.ok(Math.abs(standard(time) - value) < 2e-5, `at ${time}: ${standard(time)} vs ${value}`);
  // And it is the one the flight runs on.
  assert.match(motionSource, /curve: \[0\.4, 0, 0\.2, 1\],/);
});

test("it is picked up as it leaves and put down as it arrives: its height above the page is the shadow, and it touches down as it stops", () => {
  const liftZero = [];
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const frameMs = motion.duration / (motion.path.length - 1);
    const ms = (index) => index * frameMs;
    const speed = speeds(motion);
    const peak = Math.max(...speed);
    const left = motion.path.map((frame) => Math.hypot(...numbers(frame.translate)));
    assert.ok(motion.lift.every((value) => value >= 0 && value <= 1), `${label}: the height stays between the page and 1`);
    assert.ok(at(motion, motion.halo, 100) > 0.9, `${label}: it is in the air by 100 ms (${at(motion, motion.halo, 100)})`);
    // In the air for the first third of the time, and still well up at the middle of it...
    motion.lift.forEach((value, index) => { if (ms(index) > 70 && ms(index) <= 0.3 * motion.duration) assert.ok(value > 0.99, `${label}: lifted at ${Math.round(ms(index))} ms (${value})`); });
    assert.ok(motion.lift[Math.round(0.45 * (motion.lift.length - 1))] > 0.4, `${label}: put down too early`);
    // ...and on the page again by three quarters of it, when it has nearly stopped: set down as it arrives, never before and never after.
    motion.lift.forEach((value, index) => { if (ms(index) >= 0.75 * motion.duration) assert.ok(value < 0.01, `${label}: still lifted (${value}) at ${Math.round(ms(index))} ms of ${motion.duration}`); });
    const peakAt = motion.lift.indexOf(Math.max(...motion.lift));
    const down = motion.lift.findIndex((value, index) => index > peakAt && value < 0.01);
    liftZero.push(down / (motion.lift.length - 1));
    assert.ok(speed[down - 1] < 0.3 * peak, `${label}: it is put down at ${(speed[down - 1] / peak * 100).toFixed(0)}% of its top speed`);
    assert.ok(left[down] < 0.12 * motion.distance + 8, `${label}: it is put down with ${left[down].toFixed(0)} px still to go`);
    // It goes up once and comes down once.
    for (let index = peakAt + 1; index < motion.lift.length; index += 1) assert.ok(motion.lift[index] <= motion.lift[index - 1] + 1e-3, `${label}: the height rises again at ${Math.round(ms(index))} ms`);
    // The shadow is that height, and nothing else; it never jumps.
    motion.halo.forEach((frame, index) => assert.equal(frame.opacity, motion.lift[index], `${label}: the shadow is the height`));
    for (let index = 1; index < motion.halo.length; index += 1) {
      assert.ok(Math.abs(motion.halo[index].opacity - motion.halo[index - 1].opacity) <= 0.2, `${label}: the shadow jumps at ${index}`);
      const before = numbers(motion.halo[index - 1].translate);
      const after = numbers(motion.halo[index].translate);
      assert.ok(Math.hypot(after[0] - before[0], after[1] - before[1]) <= 3, `${label}: the shadow's place jumps at ${index}`);
    }
  }
  // It is put down at the same point of the slowing whatever way it has come (the standard curve brings it to rest, so it
  // also puts it down): by distance along the straight line a flat arc would be down while it is still rising.
  assert.ok(Math.max(...liftZero) - Math.min(...liftZero) < 0.04, `it is put down at ${liftZero.map((value) => value.toFixed(2)).join(", ")} of the time`);
});

test("the material is the height's: lifted and whole in the air, and gives way to the bubble's own fill as it is put down, never hollow and never back", () => {
  const gone = [];
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const frameMs = motion.duration / (motion.path.length - 1);
    const ms = (index) => index * frameMs;
    const speed = speeds(motion);
    const peak = Math.max(...speed);
    const left = motion.path.map((frame) => Math.hypot(...numbers(frame.translate)));
    motion.skin.forEach((frame, index) => {
      const card = motion.card[index].opacity;
      if (ms(index) > 40 && ms(index) <= 0.5 * motion.duration) assert.ok(frame.opacity > 0.995, `${label}: the skin is whole at ${Math.round(ms(index))} ms (${frame.opacity})`);
      if (ms(index) <= 0.45 * motion.duration) assert.equal(card, 0, `${label}: the bubble's own fill waits (${Math.round(ms(index))} ms)`);
      // The pill is a solid thing: once it has come up, there is never a moment when what is left of it and of the bubble's fill leaves the page showing through.
      if (ms(index) > 40) assert.ok(frame.opacity + card >= 0.999, `${label}: hollow at ${Math.round(ms(index))} ms: skin ${frame.opacity} + card ${card}`);
      if (ms(index) >= 0.85 * motion.duration) assert.deepEqual([frame.opacity, card], [0, 1], `${label}: only the bubble's own fill is left (${Math.round(ms(index))} ms)`);
    });
    // One way only: once the pill starts to give way it does not come back, and the bubble's fill does not go.
    const from = Math.round(motion.skin.length * 0.2);
    for (let index = from + 1; index < motion.skin.length; index += 1) {
      assert.ok(motion.skin[index].opacity <= motion.skin[index - 1].opacity + 1e-3, `${label}: the pill comes back at ${Math.round(ms(index))} ms`);
      assert.ok(motion.card[index].opacity >= motion.card[index - 1].opacity - 1e-3, `${label}: the bubble's fill goes at ${Math.round(ms(index))} ms`);
    }
    // The pill is gone when the capsule has nearly stopped, and not before: it gives way when it is low and slow.
    const goneAt = motion.skin.findIndex((frame, index) => index > from && frame.opacity < 0.02);
    gone.push(goneAt / (motion.skin.length - 1));
    assert.ok(speed[goneAt - 1] < 0.2 * peak, `${label}: the pill is gone at ${(speed[goneAt - 1] / peak * 100).toFixed(0)}% of the top speed`);
    assert.ok(left[goneAt] < 0.05 * motion.distance + 6, `${label}: the pill is gone with ${left[goneAt].toFixed(0)} px still to go`);
    // (Arrival is at 97% of the clock, between two frames eight milliseconds apart: 4% allows for the rounding to one of them.)
    assert.ok(at(motion, motion.skin, motion.arrive) < 0.04, `${label}: no pill left at arrival`);
    for (let index = 1; index < motion.skin.length; index += 1) {
      assert.ok(Math.abs(motion.skin[index].opacity - motion.skin[index - 1].opacity) <= 0.5, `${label}: the skin jumps at ${index}`);
      assert.ok(Math.abs(motion.card[index].opacity - motion.card[index - 1].opacity) <= 0.2, `${label}: the card jumps at ${index}`);
    }
  }
  // Like the height, it follows the clock: at the same point of the slowing whatever way the capsule has come.
  assert.ok(Math.max(...gone) - Math.min(...gone) < 0.04, `the pill is gone at ${gone.map((value) => value.toFixed(2)).join(", ")} of the time`);
});

test("a message laid out another way changes layout behind the capsule that comes up round it, in a few frames and never as two whole layouts at once", () => {
  const range = (name) => {
    const match = flight.match(new RegExp(`const ${name}: \\[number, number\\] = \\[(\\d+), (\\d+)\\];`));
    assert.ok(match, name);
    return [Number(match[1]), Number(match[2])];
  };
  const out = range("SWAP_OUT_MS");
  const into = range("SWAP_IN_MS");
  assert.ok(Math.max(out[1], into[1]) <= 60, `the exchange is over by ${Math.max(out[1], into[1])} ms`);
  assert.ok(into[0] >= out[0] - 8 && into[0] < out[1], "the new layout starts as the old one leaves, not before and not after");
  // The curve they trade on (EASE_CROSS), solved for its y at a time: a cubic Bezier.
  const cross = flight.match(/const EASE_CROSS = "cubic-bezier\(([^)]*)\)"/)[1].split(",").map(Number);
  const eased = bezier(cross);
  const run = (time, [from, to]) => (time <= from ? 0 : time >= to ? 1 : eased((time - from) / (to - from)));
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    // The skin's opacity between two frames (they are eight milliseconds apart; the exchange is quicker than that).
    const between = (time) => {
      const position = (time / motion.duration) * (motion.skin.length - 1);
      const low = Math.floor(position);
      const part = position - low;
      return motion.skin[low].opacity * (1 - part) + motion.skin[Math.min(motion.skin.length - 1, low + 1)].opacity * part;
    };
    let worstBoth = 0;
    let dimmest = 9;
    for (let time = 0; time <= 80; time += 1) {
      const skin = between(time);
      const old = (1 - skin) * (1 - run(time, out));
      const now = run(time, into);
      worstBoth = Math.max(worstBoth, Math.min(old, now));
      dimmest = Math.min(dimmest, old + now);
    }
    assert.ok(worstBoth < 0.36, `${JSON.stringify(input)}: the two layouts are both seen at ${(worstBoth * 100).toFixed(0)}% at once`);
    assert.ok(dimmest > 0.5, `${JSON.stringify(input)}: and the text is never fainter than ${(dimmest * 100).toFixed(0)}% on the way`);
  }
  // The field's text is under the shell: the skin that comes up is what it goes out behind.
  assert.match(flight, /if \(fieldLayer\) ghost\.prepend\(fieldLayer\);/);
  assert.doesNotMatch(flight, /ghost\.append\(fieldLayer\)/);
});

test("a message flies only when motion is allowed, with a window to see it in, and says why when it does not", () => {
  for (const name of ["recordSendOrigin", "recordCardOrigin", "launchSendFlight"]) {
    const body = flight.slice(flight.indexOf(`export function ${name}(`));
    assert.match(body.slice(0, 700), /flightsAllowed\(\)/, `${name} asks whether a flight may be made`);
  }
  const allowed = flight.slice(flight.indexOf("function flightsAllowed()"));
  assert.match(allowed.slice(0, 200), /prefersReducedMotionNow\(\)/, "the motion preference");
  assert.match(allowed.slice(0, 200), /document\.hidden/, "a window that is shown");
  assert.match(allowed.slice(0, 200), /pageIsCssZoomed\(\)/, "a page that is not scaled with CSS zoom (the web fallback)");
  for (const reason of ["message too tall to fly", "already in place", "no text to anchor on", "entry has no width"]) assert.ok(flight.includes(`"${reason}"`), reason);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /:root\[data-loom-reduced-motion="true"\]/);
});

test("the composers say where the text is before they clear it, and the transcript asks for the flight when an entry is born", () => {
  const base = read("components/ComposerBase.tsx");
  assert.ok(base.indexOf("recordSendOrigin(textareaRef.current)") > 0);
  assert.ok(base.indexOf("recordSendOrigin(textareaRef.current)") < base.indexOf("const request = onSend("), "recorded before onSend");
  assert.ok(base.indexOf("const request = onSend(") < base.indexOf('setValue("")'), "and before the field is cleared");
  const steering = read("components/Composer.tsx");
  assert.ok(steering.indexOf('recordSendOrigin(textareaRef.current, "steer")') > 0);
  assert.ok(steering.indexOf('recordSendOrigin(textareaRef.current, "steer")') < steering.indexOf("request = Promise.resolve(onSend("));
  const transcript = read("components/Transcript.tsx");
  assert.match(transcript, /launchSendFlight\(entry\)/);
  assert.match(transcript, /launchSendFlight\(entry, "steer"\)/);
  assert.match(transcript, /ref=\{isSteeringUserMessage\(block\.item\) && block\.item\.optimistic === true \? steeringEntryRef : undefined\}/);
  assert.match(transcript, /recordCardOrigin\(event\.currentTarget\)/);
  // Once per mount, and an unmount takes the flight with it.
  assert.match(transcript, /return \(\) => endSendFlight\(entry\);/);
});

test("the placeholder stays back until the text has left, in the composer that arrives and in the one that stays (guidance)", () => {
  // Guidance is sent from a composer that does not change: there is no crossfade, and the field's own placeholder would
  // come back under the text that is leaving. The rule that holds it back names both the composer that fades in and the
  // one that stays.
  assert.match(css, /\.composer-mode-crossfade\[data-sent\] > :is\(\.crossfade-in, \.crossfade-current\) \.composer textarea::placeholder \{\s*animation: loom-flight-placeholder/);
  const keyframes = css.match(/@keyframes loom-flight-placeholder \{([\s\S]*?)\n\}/)?.[1] ?? "";
  assert.match(keyframes, /0%, \d+% \{ opacity: 0; \}/);
  assert.doesNotMatch(keyframes, /100%/, "it ends where the placeholder's own style is, whatever that is");
  // The crossfade names the two classes this relies on.
  const crossfade = read("components/Crossfade.tsx");
  assert.match(crossfade, /"crossfade-in" : "crossfade-current"/);
});

test("the turn that has just been sent is live from the click, and no other turn can be taken for it", () => {
  const app = read("App.tsx");
  const confirmed = app.search(/const confirmedTurnRunning = (?:useMemo\(\(\) => )?Boolean\(/);
  const running = app.search(/const transcriptRunning = (?:useMemo\(\(\) => )?confirmedTurnRunning/);
  assert.ok(confirmed > 0 && running > confirmed);
  const block = app.slice(running, running + 260);
  // A message sent from the home screen waits for thread/start before the app's own busy state is set: the sending
  // message itself is what makes the turn live, not that flag.
  assert.doesNotMatch(block, /loom\.turnActive/);
  assert.match(block, /item\.type === "user_message" && item\.status === "sending"/);
  // Before turn/started, currentTurnId still names the previous turn: it is withheld until the Host has confirmed one.
  assert.match(app, /currentTurnId=\{confirmedTurnRunning \? thread\?\.currentTurnId : undefined\}/);
});

test("the real entry is held back, its capsule row is reserved, and nothing else styles the flight", () => {
  assert.match(css, /\.entry-user_message\[data-flight="flying"\] \{\s*visibility: hidden;/);
  assert.match(css, /\.turn-block\[data-flight="flying"\] \.pending-thinking-presence\[data-motion-phase\] \{\s*grid-template-rows: 1fr;\s*opacity: 0;\s*transition: none;/);
  assert.match(css, /\.entry-user_message\[data-flight\] \{\s*animation: none !important;/);
  // One owner: no other stylesheet names the flight's classes or attribute.
  for (const name of fs.readdirSync(new URL("../../src/components/", import.meta.url)).filter((file) => file.endsWith(".css") && file !== "send-flight.css")) {
    const other = strip(read(`components/${name}`));
    assert.doesNotMatch(other, /send-flight|data-flight/, name);
  }
  // The home screen is copied whole, and nothing in the copy may play again.
  assert.match(css, /\.send-flight-home \*\s*\{[^}]*animation: none !important;/);
});

test("the capsule keeps up with an entry that is still moving when it lands: its aim tightens as it comes in, from a slow filter to a quick one", () => {
  // The page's own scroll goes on easing for about a hundred milliseconds after a flight of this length is over, and the
  // hand-off is made to the place the entry is in then. A filter of 55 ms is 4 px behind an entry moving at 75 px/s.
  const match = flight.match(/Math\.exp\(-\(now - lastTrack\) \/ \((\d+) - (\d+) \* blend\)\)/);
  assert.ok(match, "the filter's time constant is a function of how far in the capsule is");
  const [slow, drop] = [Number(match[1]), Number(match[2])];
  assert.ok(slow >= 40 && slow <= 80, `a sudden change of layout is still walked into (${slow} ms)`);
  assert.ok(slow - drop >= 4 && slow - drop <= 15, `and at the hand-off the filter is ${slow - drop} ms: the capsule is where the entry is`);
  // The blend that decides how much of the aim is the entry's place now (and not the one the page will rest in) is the
  // one that tightens it: they come in together.
  assert.match(flight, /const late = Math\.max\(0, Math\.min\(1, \(progress - 0\.55\) \/ 0\.45\)\);\s*const blend = late \* late \* \(3 - 2 \* late\);/);
});

test("the flight keeps what it needs to be undone: it lands, cancels with its entry, and leaves nothing behind", () => {
  for (const needle of ["flights.delete(entry)", "ghost.remove()", "cancelAnimationFrame(tracker)", "window.clearTimeout(guard)", "window.clearTimeout(releaseTimer)", "queueMicrotask(", "if (!entry.isConnected) flights.get(entry)?.cancel();"]) {
    assert.ok(flight.includes(needle), needle);
  }
  // A paused or throttled page cannot strand the real entry: a safety net lands the flight a little after it should have.
  assert.match(flight, /const guard = window\.setTimeout\(land, duration \+ \d+\);/);
});

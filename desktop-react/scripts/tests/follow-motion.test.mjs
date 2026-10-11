import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/components/followMotion.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { advanceFollow, followStiffness, snapScrollOffset, TargetFlow, FOLLOW_MAX_FRAME_S } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

/** Run the spring toward a fixed target in steps of `ms`, until it settles or `limitMs` passes. */
function run(gap, ms, { profile = "live", flowing = false, limitMs = 2000, velocity = 0 } = {}) {
  let state = { position: -gap, velocity };
  const path = [state.position];
  let elapsed = 0;
  while (elapsed < limitMs) {
    const next = advanceFollow(state, 0, ms / 1000, profile, flowing);
    state = { position: next.position, velocity: next.velocity };
    elapsed += ms;
    path.push(state.position);
    if (next.settled) return { elapsed, path, settled: true };
  }
  return { elapsed, path, settled: false };
}

test("a line step closes smoothly: from rest, monotonic, no overshoot, home in about a fifth of a second", () => {
  for (const ms of [6, 8.3, 16.7]) {
    const { elapsed, path, settled } = run(26, ms);
    assert.ok(settled, `settles at ${ms}ms frames`);
    assert.ok(elapsed >= 150 && elapsed <= 300, `${ms}ms frames: ${elapsed}ms`);
    for (let index = 1; index < path.length; index += 1) assert.ok(path[index] >= path[index - 1] - 1e-9, "never moves backward");
    assert.ok(path.every((position) => position <= 1e-9), "never overshoots the target");
    // It starts gently: the first frames do not jump a sixth of the step.
    assert.ok(path[1] - path[0] < 26 / 6, "starts from standstill");
  }
});

test("the curve is the same on a 165 Hz display and a 60 Hz one", () => {
  const at = (ms, t) => {
    let state = { position: -48, velocity: 0 };
    for (let elapsed = 0; elapsed < t; elapsed += ms) {
      const next = advanceFollow(state, 0, ms / 1000, "live");
      state = { position: next.position, velocity: next.velocity };
    }
    return state.position;
  };
  // 200 Hz and 50 Hz frames sample the same instants.
  for (const t of [40, 100, 160, 200]) {
    assert.ok(Math.abs(at(5, t) - at(20, t)) < 0.3, `${t}ms: ${at(5, t)} vs ${at(20, t)}`);
  }
});

test("a long frame is integrated as a short one, so a hitch does not jump the whole gap", () => {
  const next = advanceFollow({ position: -26, velocity: 0 }, 0, 0.4, "live");
  const bounded = advanceFollow({ position: -26, velocity: 0 }, 0, FOLLOW_MAX_FRAME_S, "live");
  assert.equal(next.position, bounded.position);
  assert.ok(next.position < -1, "and the view is still on its way");
});

test("a bigger gap is closed by a stiffer spring, up to a bound, so a large arrival is not left behind", () => {
  const stiffness = (gap, profile = "live", flowing = false) => followStiffness(profile, gap, flowing);
  assert.equal(stiffness(0), stiffness(36), "the base stiffness holds up to the free gap");
  assert.ok(stiffness(100) > stiffness(36));
  assert.ok(stiffness(300) > stiffness(100));
  assert.equal(stiffness(5000), stiffness(100000), "and is bounded");
  assert.ok(stiffness(26, "travel") < stiffness(26, "live"), "travelling (sending, completion) is the gentler one");
  assert.ok(stiffness(26, "live", true) > 3 * stiffness(26), "an animation growing the page is followed closely");
  // A 700px arrival is home inside a quarter of a second, and never runs past the edge (a stiff spring let go of
  // into a soft one did, by 57px).
  const arrival = run(700, 16.7);
  assert.ok(arrival.elapsed <= 250);
  assert.ok(arrival.path.every((position) => position <= 1e-9), "no overshoot");
  // ...and it slows into the edge rather than being stopped at it: the last 30px of a 300px arrival take several
  // frames (a stiff spring let go into a soft one is cut short by the clamp and arrives in one).
  const approach = run(300, 16.7).path.filter((position) => position < -0.4 && position > -30);
  assert.ok(approach.length >= 3, `${approach.length} frames of approach`);
  // A 26px step takes noticeably longer than the animation-following spring does to cover the same ground.
  assert.ok(run(26, 6).elapsed > run(26, 6, { flowing: true }).elapsed * 2);
});

test("a target that moves again mid-glide is followed without the view ever turning back", () => {
  let state = { position: 0, velocity: 0 };
  let target = 26;
  const positions = [];
  const gaps = [];
  for (let frame = 0; frame < 200; frame += 1) {
    if (frame % 30 === 0 && frame > 0 && frame <= 120) target += 26; // a line every ~180ms
    const next = advanceFollow(state, target, 0.006, "live");
    state = { position: next.position, velocity: next.velocity };
    positions.push(state.position);
    gaps.push(target - state.position);
  }
  for (let index = 1; index < positions.length; index += 1) assert.ok(positions[index] >= positions[index - 1] - 1e-9);
  assert.equal(state.position, target, "and it arrives");
  // A steady run of lines keeps the view within about a line of the edge: it never falls further behind.
  assert.ok(Math.max(...gaps.slice(60, 125)) < 40, `behind by ${Math.max(...gaps.slice(60, 125))}px`);
});

test("it settles exactly on the target, with no residual velocity to creep", () => {
  const { path, settled } = run(26, 6);
  assert.ok(settled);
  assert.equal(path.at(-1), 0);
  let again = advanceFollow({ position: 0, velocity: 0 }, 0, 0.006, "live");
  assert.deepEqual([again.position, again.velocity, again.settled], [0, 0, true]);
});

test("what is written to the page sits on a whole device pixel", () => {
  assert.equal(snapScrollOffset(335.31, 1.5), 335.3333333333333);
  for (const value of [0, 1.2, 99.9, 1234.567]) {
    const written = snapScrollOffset(value, 1.5);
    assert.ok(Math.abs(written * 1.5 - Math.round(written * 1.5)) < 1e-9);
    assert.ok(Math.abs(written - value) <= 1 / 3 + 1e-9);
  }
  assert.equal(snapScrollOffset(12.4, 1), 12);
  assert.equal(snapScrollOffset(12.4, 0), 12, "a nonsense ratio falls back to whole pixels");
});

test("a page growing in single steps is not 'flowing'; one growing frame after frame is", () => {
  const flow = new TargetFlow();
  flow.note(1000, 0);
  flow.note(1026, 500); // one line
  assert.equal(flow.flowing(510), false);
  flow.note(1026, 520); // unchanged page: nothing moved
  const animated = new TargetFlow();
  let target = 2000;
  animated.note(target, 0);
  for (let t = 6; t <= 30; t += 6) { target += 8; animated.note(target, t); }
  assert.equal(animated.flowing(30), true);
  assert.equal(animated.flowing(30 + 200), false, "and it is over once the page has been still for a moment");
  // Two moves close together are still a step (a line and the margin that came with it).
  const pair = new TargetFlow();
  pair.note(0, 0); pair.note(26, 6); pair.note(40, 12);
  assert.equal(pair.flowing(12), false);
});

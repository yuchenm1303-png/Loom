import assert from "node:assert/strict";
import test from "node:test";
import { blendAnchor, magnetAmplitude, stepSpring } from "../../src/components/portalLiquidMotion.ts";

test("a pinned lens ignores free pointer travel and releases continuously", () => {
  assert.equal(blendAnchor(100, 500, 1), 500);
  assert.equal(blendAnchor(900, 500, 1), 500);
  const released = Array.from({ length: 101 }, (_, index) => blendAnchor(900, 500, 1 - index / 100));
  assert.equal(released[0], 500);
  assert.equal(released.at(-1), 900);
  assert.ok(released.every((value, index) => index === 0 || value > released[index - 1]));
  assert.equal(blendAnchor(900, 500, 1.1), 500);
  assert.equal(blendAnchor(900, 500, -0.1), 900);
});

test("every mount point can lean, and the lean is sized to the control", () => {
  assert.equal(magnetAmplitude({ brand: true, width: 104, height: 34 }), 8);
  assert.equal(magnetAmplitude({ width: 74, height: 36, active: true }), 7, "the control under the lens leans less than its neighbours");
  assert.equal(magnetAmplitude({ width: 74, height: 36 }), 10);
  assert.equal(magnetAmplitude({ width: 40, height: 20 }), 10, "the theme switch leans like any other mount");
  assert.equal(magnetAmplitude({ typingSurface: true, width: 600, height: 56 }), 3, "a typing surface would pull the caret away");
  assert.equal(magnetAmplitude({ width: 600, height: 80 }), 4, "a large block takes a small lean");
  assert.equal(magnetAmplitude({ width: 520, height: 56 }), 10, "the default range is exclusive");
});

test("snap springs converge across frame rates and survive interrupted release", () => {
  for (const fps of [30, 60, 144]) {
    const spring = { value: 0, target: 1, velocity: 0 };
    for (let frame = 0; frame < fps * 2; frame++) stepSpring(spring, 1 / fps, 220, 18);
    assert.ok(Math.abs(spring.value - 1) < 0.001);
    spring.target = 0;
    for (let frame = 0; frame < fps / 10; frame++) stepSpring(spring, 1 / fps, 220, 18);
    spring.target = 1;
    for (let frame = 0; frame < fps * 2; frame++) stepSpring(spring, 1 / fps, 220, 18);
    assert.ok(Math.abs(spring.value - 1) < 0.001);
    assert.ok(Math.abs(spring.velocity) < 0.001);
  }
});

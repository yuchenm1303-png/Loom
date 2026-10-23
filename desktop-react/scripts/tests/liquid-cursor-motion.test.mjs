import assert from "node:assert/strict";
import test from "node:test";
import { blendAnchor, stepSpring } from "../../src/components/portalLiquidMotion.ts";

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

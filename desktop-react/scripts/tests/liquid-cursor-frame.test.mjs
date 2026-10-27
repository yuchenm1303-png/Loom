import assert from "node:assert/strict";
import test from "node:test";
import { lensFrameFor, LENS_STRENGTH } from "../../src/components/portalLiquidFrame.ts";

const viewport = { width: 1700, height: 940 };
const dataset = (values = {}) => ({ dataset: values });
const box = (left, top, width, height) => ({ left, top, width, height });

test("a control is framed whole, with one padding, and stays centred on itself", () => {
  const frame = lensFrameFor(dataset(), box(100, 200, 160, 46), viewport);
  assert.equal(frame.width, 180);
  assert.equal(frame.height, 66);
  assert.equal(frame.centerX, 180);
  assert.equal(frame.centerY, 223);
  assert.equal(frame.reading, false);
  assert.equal(frame.strength, LENS_STRENGTH.control);
});

test("a wide control is capped instead of becoming a band", () => {
  const frame = lensFrameFor(dataset(), box(80, 400, 780, 40), viewport);
  assert.equal(frame.width, 300);
  assert.equal(frame.centerX, 470);
});

test("a tall control keeps a readable cap", () => {
  const frame = lensFrameFor(dataset(), box(80, 400, 170, 160), viewport);
  assert.equal(frame.width, 190);
  assert.equal(frame.height, 92);
  assert.equal(frame.centerY, 480);
});

test("a reading stop is capped at 420 and gets the calm optical figure", () => {
  const frame = lensFrameFor(dataset({ liquidLens: "read", liquidSnapPad: "6" }), box(80, 447, 820, 27), viewport);
  assert.equal(frame.width, 420);
  assert.equal(frame.height, 58, "a one-line row gets the minimum readable height");
  assert.equal(frame.reading, true);
  assert.equal(frame.strength, LENS_STRENGTH.read);
  assert.ok(frame.strength < LENS_STRENGTH.control && LENS_STRENGTH.control < LENS_STRENGTH.free);
});

test("a short reading stop keeps its own measure and a tighter crop", () => {
  const frame = lensFrameFor(dataset({ liquidLens: "read", liquidSnapPad: "8" }), box(80, 300, 180, 30), viewport);
  assert.equal(frame.width, 196);
  assert.equal(frame.height, 58);
});

test("the padding a stop asks for is clamped and the viewport is honoured", () => {
  assert.equal(lensFrameFor(dataset({ liquidSnapPad: "99" }), box(0, 0, 100, 30), viewport).height, 58);
  assert.equal(lensFrameFor(dataset({ liquidSnapPad: "1" }), box(0, 0, 100, 30), viewport).height, 38);
  const narrow = lensFrameFor(dataset(), box(0, 0, 460, 40), { width: 380, height: 300 });
  assert.equal(narrow.width, 300, "the control cap still wins in a narrow window");
  const narrowRead = lensFrameFor(dataset({ liquidLens: "read" }), box(0, 0, 460, 40), { width: 380, height: 300 });
  assert.equal(narrowRead.width, 360, "a reading lens is never wider than the window");
});

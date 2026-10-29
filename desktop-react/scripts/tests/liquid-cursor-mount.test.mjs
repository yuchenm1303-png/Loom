import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { mountOwner } from "../../src/components/portalLiquidMount.ts";

/*
 * The lens frames one box. A field is built from several: the title that names
 * it, the shell that draws the box, the input that takes the keystrokes. The
 * lens used to frame whichever of them the pointer was nearest, so the same
 * field answered with a small lens over its text and a wide one over its
 * title. These tests keep a field to one lens, and keep the marks the lens
 * redraws on the page.
 */

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const cursor = read("../../src/components/PortalLiquidCursor.tsx");
const portal = read("../../src/components/WebPortal.tsx");
const dialogCss = read("../../src/components/portal-auth-dialog.css");

/** A stand-in for a DOM element: the owner rule only asks these things of one. */
const element = (tagName, { dataset = {}, control = null, controls = [] } = {}) => ({
  tagName,
  dataset,
  control,
  querySelectorAll: () => controls,
});

test("a field's title frames the control it names", () => {
  const input = element("INPUT");
  assert.equal(mountOwner(element("LABEL", { control: input })), input);
});

test("a field's shell frames the control inside it", () => {
  const input = element("INPUT");
  assert.equal(mountOwner(element("DIV", { controls: [input] })), input);
});

test("a label that wraps the control frames it too", () => {
  const input = element("INPUT");
  assert.equal(mountOwner(element("LABEL", { controls: [input] })), input);
});

test("a label whose control is a button still frames the field's input", () => {
  const input = element("INPUT");
  const reset = element("BUTTON");
  assert.equal(mountOwner(element("LABEL", { control: reset, controls: [input] })), input);
});

test("a control with no field inside frames itself", () => {
  const button = element("BUTTON");
  assert.equal(mountOwner(button), button);
});

test("a reading stop frames the words it is made of", () => {
  const line = element("SPAN", { dataset: { liquidLens: "read" } });
  assert.equal(mountOwner(line), line);
});

test("the lens frames the control, and the click belongs to it", () => {
  assert.ok(cursor.includes("const ownerOf = (element: HTMLElement): HTMLElement =>"), "the lens resolves what a mount point stands for");
  assert.ok(cursor.includes("const frame = paddedMount(ownerOf(target), rect);"), "the lens is sized on the control, not on the element under the pointer");
  assert.ok(cursor.includes("const rect = owner.getBoundingClientRect();"), "the capture follows the framed control");
  assert.ok(cursor.includes("const action = owner.matches(actionSelector)"), "a click on a field's title belongs to the field");
});

test("the sign-in card's password field is one control", () => {
  assert.ok(!portal.includes('<label className="loom-password-field">'), "the field is a group: a label that wraps the reset button names the button, not the field");
  assert.ok(portal.includes('<label className="loom-field-name" htmlFor="loomAccountPassword">'), "the title names the input");
  assert.ok(portal.includes('id="loomAccountPassword"'), "the input is the control the title names");
});

test("the Google mark is drawn, so the lens can draw it too", () => {
  assert.ok(portal.includes('<Google size={18} className="loom-google-mark" />'), "the mark is an icon, not a letter");
  assert.ok(!portal.includes('loom-google-mark" aria-hidden="true">G</span>'), "the letter is gone");
  const start = dialogCss.indexOf(".loom-editorial .editorial-panel .loom-google-mark {");
  assert.ok(start >= 0, "the mark is styled");
  const rule = dialogCss.slice(start, dialogCss.indexOf("}", start));
  assert.ok(!/background-clip:\s*text/.test(rule), "a canvas cannot clip a gradient to glyphs, so the mark is not painted that way");
  assert.ok(!/color:\s*transparent/.test(rule), "the mark is not painted as transparent text");
});

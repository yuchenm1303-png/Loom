import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(
  new URL("../../src/terminalInput.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { createTerminalInputPump } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));

test("typing behind a slow IPC acknowledgement becomes one ordered batch", async () => {
  const sends = [];
  const held = [];
  const pump = createTerminalInputPump({
    send(text) {
      sends.push(text);
      return new Promise((resolve) => held.push(resolve));
    },
    writable: () => true,
    activity() {},
    error(error) {
      throw error;
    },
  });
  pump.write("a");
  await tick();
  const text = "中文🚀" + "b".repeat(500) + "\t\x1b[A\x03\r";
  for (const char of text) pump.write(char);
  await tick();
  assert.deepEqual(sends, ["a"], "only one write can be in flight");
  held.shift()();
  await tick();
  assert.deepEqual(
    sends,
    ["a", text],
    "hundreds of keys do not cost hundreds of round trips",
  );
  held.shift()();
  pump.dispose();
});

test("a large paste is bounded without splitting Unicode or reordering controls", async () => {
  const sends = [];
  const text = "x".repeat(32767) + "🚀" + "y".repeat(40000) + "\r";
  const pump = createTerminalInputPump({
    send: async (text) => {
      sends.push(text);
    },
    writable: () => true,
    activity() {},
    error(error) {
      throw error;
    },
  });
  pump.write(text);
  await tick();
  assert.equal(sends.join(""), text);
  assert.ok(
    sends.every(
      (chunk) => chunk.length <= 32768 && !/[\uD800-\uDBFF]$/.test(chunk),
    ),
  );
  pump.dispose();
});

test("failed or disposed input is never replayed as a command", async () => {
  let reject;
  const sends = [],
    errors = [];
  const pump = createTerminalInputPump({
    send(text) {
      sends.push(text);
      return new Promise((_, fail) => {
        reject = fail;
      });
    },
    writable: () => true,
    activity() {},
    error(error) {
      errors.push(error);
    },
  });
  pump.write("first");
  await tick();
  pump.write("must not run\r");
  reject(new Error("disconnected"));
  await tick();
  assert.deepEqual(sends, ["first"]);
  assert.equal(errors.length, 1);
  pump.write("disposed\r");
  pump.dispose();
  await tick();
  assert.deepEqual(sends, ["first"]);
});

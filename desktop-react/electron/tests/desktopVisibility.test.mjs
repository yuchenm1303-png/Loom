import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function productionFunction(file, name, scope) {
  const source = ts.createSourceFile(file, readFileSync(new URL("../" + file, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
  const declaration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(declaration);
  const js = ts.transpileModule(declaration.getText(source).replace(/^export /, ""), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(...Object.keys(scope), `${js}; return ${name};`)(...Object.values(scope));
}

test("Desktop launches are visible while background Hosts stay hidden", () => {
  const launches = [];
  const launch = productionFunction("hostProcess.ts", "launch", {
    app: { isPackaged: true }, process: { execPath: "Loom.exe", env: {} }, hostDataPath: "profile",
    spawn: (exe, args, options) => { launches.push({ args, options }); return { on() {}, unref() {} }; },
  });
  launch([], false);
  launch(["--loom-host"], true);
  assert.equal(launches[0].options.windowsHide, false);
  assert.equal(launches[1].options.windowsHide, true);
});

test("reopening restores minimized Desktop, taskbar and foreground before confirmation", () => {
  let visible = false;
  let minimized = true;
  const events = [];
  const show = productionFunction("main.ts", "showDesktopWindow", {
    isHostProcess: false, createWindow: () => assert.fail("existing window should be reused"),
    mainWindow: {
      isDestroyed: () => false, isMinimized: () => minimized, isVisible: () => visible,
      setSkipTaskbar: value => events.push(["taskbar", value]),
      restore: () => { minimized = false; events.push("restore"); },
      show: () => { visible = true; events.push("show"); },
      moveTop: () => events.push("top"), focus: () => events.push("focus"),
    },
  });
  assert.equal(show(), true);
  assert.deepEqual(events, [["taskbar", false], "restore", "show", "top", "focus"]);
});

test("a window still hidden after show is not acknowledged as visible", () => {
  const show = productionFunction("main.ts", "showDesktopWindow", {
    isHostProcess: false, createWindow() {}, mainWindow: {
      isDestroyed: () => false, isMinimized: () => false, isVisible: () => false,
      setSkipTaskbar() {}, show() {}, moveTop() {}, focus() {},
    },
  });
  assert.equal(show(), false);
});

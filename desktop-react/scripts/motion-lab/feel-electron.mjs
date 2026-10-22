// Motion lab: the feel probe (feel-lib.mjs) in a real Electron window: the GPU, the display's real refresh rate, real
// input events through the window. This is where the milliseconds in docs/motion-feel-2026-10-21.md come from; build the
// live-app fixture for production first (React in development mode doubles its own cost).
//   FEEL_ORIGIN=http://127.0.0.1:5188 [FEEL_ONLY=a,b] [FEEL_THEME=light] [FEEL_OUT=run.json] node_modules/electron/dist/electron.exe scripts/motion-lab/feel-electron.mjs
// The origin comes in the environment: Electron quits before running a script given an http:// argument. The window
// sits almost entirely past the right edge of the screen; a 60px sliver stays visible so Windows does not treat it as
// occluded and throttle its frames. Compare two runs with feel-compare.mjs.
import { app, BrowserWindow, screen } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { SAMPLER, SCENARIOS, report } from "./feel-lib.mjs";

const origin = process.env.FEEL_ORIGIN;
const only = process.env.FEEL_ONLY ? new Set(process.env.FEEL_ONLY.split(",")) : null;
const theme = process.env.FEEL_THEME || "dark";
app.setPath("userData", path.join(os.tmpdir(), "loom-feel-electron"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const display = screen.getPrimaryDisplay();
  const area = display.workArea;
  const win = new BrowserWindow({
    width: 1440, height: 900, x: area.x + area.width - 60, y: area.y + 20, frame: false, show: false, skipTaskbar: true,
    backgroundColor: theme === "light" ? "#f7f7f8" : "#0d0e11",
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  await win.loadURL(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en`);
  win.setAlwaysOnTop(true, "floating");
  win.showInactive();
  const js = (code) => win.webContents.executeJavaScript(code);
  await js(`new Promise((resolve) => { const ready = () => document.querySelector(".thread-header") ? resolve(true) : setTimeout(ready, 50); ready(); })`);
  await js(SAMPLER);
  console.log(`display ${display.size.width}x${display.size.height} @${display.displayFrequency}Hz, scale ${display.scaleFactor}`);
  await wait(1800);
  const move = (x, y) => win.webContents.sendInputEvent({ type: "mouseMove", x, y });
  move(700, 450);
  const play = async ([kind, spec, dx, dy]) => {
    if (kind === "wait") return wait(spec);
    if (kind === "key") {
      win.webContents.sendInputEvent({ type: "keyDown", keyCode: spec });
      win.webContents.sendInputEvent({ type: "keyUp", keyCode: spec });
      return undefined;
    }
    const point = await js(`window.__feel.point(${JSON.stringify(spec)}, ${dx ?? "null"}, ${dy ?? "null"})`);
    if (!point) throw new Error(`not found: ${spec}`);
    const button = kind === "rclick" ? "right" : "left";
    move(point.x, point.y);
    await wait(90);
    win.webContents.sendInputEvent({ type: "mouseDown", x: point.x, y: point.y, button, clickCount: 1 });
    await wait(16);
    win.webContents.sendInputEvent({ type: "mouseUp", x: point.x, y: point.y, button, clickCount: 1 });
    return undefined;
  };
  const results = [];
  for (const sc of SCENARIOS) {
    if (only && !only.has(sc.name) && !sc.prep) continue;
    await js(`(() => { const f = window.__feel; f.subjects = ${JSON.stringify(sc.subjects)}; f.frames = []; f.inputs = []; f.on = true; })()`);
    await wait(60);
    try { for (const step of sc.steps) await play(step); } catch (error) { console.log(`! ${sc.name}: ${error.message}`); }
    await wait(sc.wait);
    const { frames, inputs } = await js(`(() => { const f = window.__feel; f.on = false; return { frames: f.frames, inputs: f.inputs }; })()`);
    if (!inputs.length) { console.log(`! ${sc.name}: no input reached the page`); continue; }
    if (sc.prep && only && !only.has(sc.name)) continue;
    results.push(report(sc, frames, inputs));
  }
  if (process.env.FEEL_OUT) fs.writeFileSync(process.env.FEEL_OUT, JSON.stringify(results, null, 1));
  app.quit();
});

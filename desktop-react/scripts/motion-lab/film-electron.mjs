// Motion lab: film what a real Electron window shows, every frame the compositor presents, at real speed (no slow
// motion, no paused clocks: View Transitions, FLIP, CSS and React all run as they do for the user). The frames come from
// webContents.beginFrameSubscription, cropped to a clip, and are laid out in one sheet with the ms since the input.
//   FILM_ORIGIN=http://127.0.0.1:5191 [FILM_SCENE=sidebar] [FILM_THEME=dark] [FILM_CLIP=x,y,w,h] [FILM_MS=640]
//   [FILM_EVERY=1] [FILM_OUT=dir] [FILM_QUERY=&recent=4] node_modules/electron/dist/electron.exe scripts/motion-lab/film-electron.mjs
// Scenes are lists of steps: ["click", selector] presses the element's centre with real input events (the pointer then
// rests there, as a hand would), ["film", label] records FILM_MS of frames from the next input, ["wait", ms]. The origin
// comes in the environment: Electron quits before running a script given an http:// argument. The window sits almost
// entirely past the right edge of the screen (a 60px sliver stays visible so Windows does not throttle it).
import { app, BrowserWindow, screen } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const origin = process.env.FILM_ORIGIN;
const theme = process.env.FILM_THEME || "dark";
const sceneName = process.env.FILM_SCENE || "sidebar";
const filmMs = Number(process.env.FILM_MS || 640);
const every = Number(process.env.FILM_EVERY || 1);
const out = process.env.FILM_OUT || path.join(os.tmpdir(), `film-${sceneName}-${theme}`);
const query = process.env.FILM_QUERY || "";
const clip = (process.env.FILM_CLIP || "0,0,520,60").split(",").map(Number);
app.setPath("userData", path.join(os.tmpdir(), "loom-film-electron"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const SCENES = {
  // Close from the card's own toggle, then open from the header's, each filmed from the click.
  sidebar: [
    ["film", "close"], ["click", ".sidebar-controls .sidebar-toggle"], ["wait", 900],
    ["film", "open"], ["click", ".sidebar-controls .sidebar-toggle"], ["wait", 900],
  ],
  // A second click while the first is still moving.
  "sidebar-reverse": [
    ["film", "close-reopen"], ["click", ".sidebar-controls .sidebar-toggle"], ["wait", 140],
    ["click", ".sidebar-controls .sidebar-toggle"], ["wait", 900],
  ],
};

app.whenReady().then(async () => {
  const display = screen.getPrimaryDisplay();
  const area = display.workArea;
  const win = new BrowserWindow({
    width: 1440, height: 900, x: area.x + area.width - 60, y: area.y + 20, frame: false, show: false, skipTaskbar: true,
    backgroundColor: theme === "light" ? "#f7f7f8" : "#0d0e11",
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  await win.loadURL(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en${query}`);
  win.setAlwaysOnTop(true, "floating");
  win.showInactive();
  const js = (code) => win.webContents.executeJavaScript(code);
  await js(`new Promise((resolve) => { const ready = () => document.querySelector(".thread-header") ? resolve(true) : setTimeout(ready, 50); ready(); })`);
  await wait(1800);
  const scale = display.scaleFactor;
  const move = (x, y) => win.webContents.sendInputEvent({ type: "mouseMove", x, y });
  move(700, 450);

  let recording = null;
  let counter = 0;
  win.webContents.beginFrameSubscription(false, (image) => {
    if (!recording) return;
    counter += 1;
    if ((counter - 1) % every) return;
    const size = image.getSize();
    const ratio = size.width / 1440;
    const rect = { x: Math.round(clip[0] * ratio), y: Math.round(clip[1] * ratio), width: Math.round(clip[2] * ratio), height: Math.round(clip[3] * ratio) };
    recording.frames.push({ t: performance.now(), png: image.crop(rect).toPNG() });
  });

  fs.mkdirSync(out, { recursive: true });
  const films = [];
  let pending = null;
  for (const [kind, spec] of SCENES[sceneName]) {
    if (kind === "wait") { await wait(spec); continue; }
    if (kind === "film") { pending = spec; continue; }
    if (kind === "click") {
      const point = await js(`(() => { const el = document.querySelector(${JSON.stringify(spec)}); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
      if (!point) { console.log(`! not found: ${spec}`); continue; }
      move(point.x, point.y);
      await wait(260);
      if (pending) {
        recording = { label: pending, frames: [], start: 0 };
        films.push(recording);
        pending = null;
        await wait(50);
      }
      const film = recording;
      film.start ||= performance.now();
      win.webContents.sendInputEvent({ type: "mouseDown", x: point.x, y: point.y, button: "left", clickCount: 1 });
      await wait(16);
      win.webContents.sendInputEvent({ type: "mouseUp", x: point.x, y: point.y, button: "left", clickCount: 1 });
      if (film) setTimeout(() => { if (recording === film) recording = null; }, filmMs);
    }
  }
  await wait(filmMs + 100);
  recording = null;
  win.webContents.endFrameSubscription();

  // A sheet per film: the frames in columns, the ms since the input above each one.
  const cols = Number(process.env.FILM_COLS || 2);
  for (const film of films) {
    const dir = path.join(out, film.label);
    fs.mkdirSync(dir, { recursive: true });
    const cells = film.frames.map((frame, index) => {
      const ms = Math.round(frame.t - film.start);
      fs.writeFileSync(path.join(dir, `${String(index).padStart(3, "0")}_${ms}ms.png`), frame.png);
      return `<figure style="margin:0"><figcaption style="height:14px">${film.label} ${ms} ms</figcaption><img style="display:block;width:${clip[2]}px;height:${clip[3]}px" src="data:image/png;base64,${frame.png.toString("base64")}"></figure>`;
    });
    const html = `<html><body style="margin:0;padding:6px;background:#2a2c33;font:11px Segoe UI;color:#ccc;display:grid;grid-template-columns:repeat(${cols},${clip[2]}px);gap:4px 10px">${cells.join("")}</body></html>`;
    const sheetHtml = path.join(out, `${film.label}.html`);
    fs.writeFileSync(sheetHtml, html);
    const width = 12 + cols * clip[2] + (cols - 1) * 10;
    const height = 12 + Math.ceil(cells.length / cols) * (clip[3] + 18);
    const sheetWin = new BrowserWindow({ width, height, useContentSize: true, show: false, enableLargerThanScreen: true, webPreferences: { offscreen: true } });
    sheetWin.setContentSize(width, height);
    await sheetWin.loadFile(sheetHtml);
    await wait(300);
    const image = await sheetWin.webContents.capturePage({ x: 0, y: 0, width, height });
    fs.writeFileSync(path.join(out, `${film.label}.png`), image.toPNG());
    sheetWin.destroy();
    console.log(`${film.label}: ${film.frames.length} frames -> ${path.join(out, `${film.label}.png`)}`);
  }
  console.log(`display ${display.size.width}x${display.size.height} @${display.displayFrequency}Hz scale ${scale}`);
  app.quit();
});

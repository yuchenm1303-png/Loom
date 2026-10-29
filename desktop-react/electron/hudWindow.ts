import { BrowserWindow, screen } from "electron";
import { hudDocument } from "./hudDocument.js";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const HIDE_AFTER_MS = 220;

let hudWindow: BrowserWindow | null = null;
let lastBounds = { x: 0, y: 0, width: 1440, height: 900 };
let pendingPayload: Record<string, unknown> | null = null;
let hideTimer: NodeJS.Timeout | null = null;
let displayListenersRegistered = false;

function virtualDesktopBounds(): Electron.Rectangle {
  const displays = screen.getAllDisplays();
  if (!displays.length) return screen.getPrimaryDisplay().bounds;
  let left = displays[0].bounds.x;
  let top = displays[0].bounds.y;
  let right = displays[0].bounds.x + displays[0].bounds.width;
  let bottom = displays[0].bounds.y + displays[0].bounds.height;
  for (const display of displays.slice(1)) {
    left = Math.min(left, display.bounds.x);
    top = Math.min(top, display.bounds.y);
    right = Math.max(right, display.bounds.x + display.bounds.width);
    bottom = Math.max(bottom, display.bounds.y + display.bounds.height);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function isVisiblePayload(payload: Record<string, unknown> | null): payload is Record<string, unknown> {
  return Boolean(payload && payload.visible !== false);
}

function terminalPayload(payload: Record<string, unknown>): boolean {
  return payload.terminal === true;
}

function clearTimer(timer: NodeJS.Timeout | null): void {
  if (timer) clearTimeout(timer);
}

function registerDisplayListeners(): void {
  if (displayListenersRegistered) return;
  displayListenersRegistered = true;
  screen.on("display-added", resizeHudWindow);
  screen.on("display-removed", resizeHudWindow);
  screen.on("display-metrics-changed", resizeHudWindow);
}


function translatePayload(payload: Record<string, unknown>): Record<string, unknown> {
  const next = { ...payload };
  const rawX = next.screenX ?? next.screen_x;
  const rawY = next.screenY ?? next.screen_y;
  const sx = rawX == null ? NaN : Number(rawX);
  const sy = rawY == null ? NaN : Number(rawY);
  if (Number.isFinite(sx) && Number.isFinite(sy)) {
    next.hudX = sx - lastBounds.x;
    next.hudY = sy - lastBounds.y;
  }
  return next;
}

function resizeHudWindow(): void {
  if (!hudWindow || hudWindow.isDestroyed()) return;
  lastBounds = virtualDesktopBounds();
  hudWindow.setBounds(lastBounds, false);
  if (hudWindow.isVisible()) {
    hudWindow.setAlwaysOnTop(true, "screen-saver");
    hudWindow.moveTop();
  }
}

function showHudWindow(window: BrowserWindow): void {
  clearTimer(hideTimer);
  hideTimer = null;
  resizeHudWindow();
  if (!window.isVisible()) {
    window.setAlwaysOnTop(true, "screen-saver");
    window.showInactive();
    window.moveTop();
  }
}

function hideHudWindow(delayMs = HIDE_AFTER_MS): void {
  clearTimer(hideTimer);
  if (!hudWindow || hudWindow.isDestroyed()) return;
  hideTimer = setTimeout(() => {
    if (!hudWindow || hudWindow.isDestroyed()) return;
    hudWindow.hide();
  }, delayMs);
}

export function createHudOverlayWindow(force = false): BrowserWindow | null {
  if (hudWindow && !hudWindow.isDestroyed()) return hudWindow;
  if (!force && !isVisiblePayload(pendingPayload)) return null;

  registerDisplayListeners();
  lastBounds = virtualDesktopBounds();
  hudWindow = new BrowserWindow({
    ...lastBounds,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    closable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: false,
    show: false,
    title: "Loom HUD Overlay",
    webPreferences: {
      preload: path.join(__dirname, "hudPreload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });

  // The HUD is display-only. Do not forward mouse movement through the overlay:
  // on Windows, a transparent always-on-top window with forwarded mouse events
  // can force repeated cursor hit-testing/repainting over the main Loom window.
  hudWindow.setIgnoreMouseEvents(true);
  // Keep the diagnostic overlay visible to the user but out of screenshots
  // consumed by the model.  CSS capture-safe only hides child content and was
  // never a security boundary; Windows content protection is.
  hudWindow.setContentProtection(true);
  hudWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  hudWindow.once("ready-to-show", () => {
    if (!hudWindow || hudWindow.isDestroyed() || !isVisiblePayload(pendingPayload)) return;
    sendHudUpdate(pendingPayload);
  });
  hudWindow.on("closed", () => {
    hudWindow = null;
  });
  void hudWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(hudDocument())}`);
  return hudWindow;
}

export function sendHudUpdate(payload: Record<string, unknown>): void {
  if (!isVisiblePayload(payload)) {
    pendingPayload = null;
    const existing = hudWindow && !hudWindow.isDestroyed() ? hudWindow : null;
    if (existing && !existing.webContents.isLoading()) {
      existing.webContents.send("loom:hud-update", { ...(payload as Record<string, unknown>), visible: false });
    }
    hideHudWindow();
    return;
  }

  pendingPayload = payload;
  const window = hudWindow && !hudWindow.isDestroyed() ? hudWindow : createHudOverlayWindow(true);
  if (!window) return;
  showHudWindow(window);
  if (window.webContents.isLoading()) return;

  window.webContents.send("loom:hud-update", translatePayload(payload));
  if (terminalPayload(payload)) hideHudWindow(2300);
}

export function closeHudOverlayWindow(): void {
  clearTimer(hideTimer);
  hideTimer = null;
  if (!hudWindow || hudWindow.isDestroyed()) return;
  hudWindow.destroy();
  hudWindow = null;
}

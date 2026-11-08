import { BrowserWindow, ipcMain } from "electron";

const windows = new Map<BrowserWindow["webContents"], BrowserWindow>();
let installed = false;
export function installWindowChrome(window: BrowserWindow): void {
  const channel = "loom:window-control";
  const contents = window.webContents;
  windows.set(contents, window);
  window.on("closed", () => windows.delete(contents));
  if (installed) return;
  installed = true;
  ipcMain.handle(channel, (event, action: string, point?: { x: number; y: number }) => {
    const window = windows.get(event.sender);
    if (!window || event.senderFrame !== window.webContents.mainFrame || window.isDestroyed()) return null;
    if (action === "minimize") window.minimize();
    else if (action === "maximize") window.isMaximized() ? window.unmaximize() : window.maximize();
    else if (action === "close") window.close();
    else if (action === "move" && !window.isMaximized() && point
      && Number.isFinite(point.x) && Number.isFinite(point.y)) window.setPosition(Math.round(point.x), Math.round(point.y));
    if (window.isDestroyed()) return null;
    return { maximized: window.isMaximized(), ...window.getBounds() };
  });
}

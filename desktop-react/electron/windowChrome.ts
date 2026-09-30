import { BrowserWindow, ipcMain } from "electron";

export function installWindowChrome(window: BrowserWindow): void {
  const channel = "loom:window-control";
  ipcMain.removeHandler(channel);
  ipcMain.handle(channel, (event, action: string, point?: { x: number; y: number }) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || window.isDestroyed()) return null;
    if (action === "minimize") window.minimize();
    else if (action === "maximize") window.isMaximized() ? window.unmaximize() : window.maximize();
    else if (action === "close") window.close();
    else if (action === "move" && !window.isMaximized() && point
      && Number.isFinite(point.x) && Number.isFinite(point.y)) window.setPosition(Math.round(point.x), Math.round(point.y));
    if (window.isDestroyed()) return null;
    return { maximized: window.isMaximized(), ...window.getBounds() };
  });
  window.on("closed", () => ipcMain.removeHandler(channel));
}

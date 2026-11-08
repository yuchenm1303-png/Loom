import type { BrowserWindow, IpcMainInvokeEvent } from "electron";

// The preload exposes window.loom to whatever document is in a window's main
// frame. Only Loom's own document in the desktop window may use it; other
// windows (artifact previews, the HUD), iframes, and anything the window has
// been navigated to are refused.
const rendererWindows = new Map<BrowserWindow["webContents"], { window: BrowserWindow; origin: string; document: string }>();
export function trustedRendererWindows(): BrowserWindow[] {
  return [...rendererWindows.values()].map(entry => entry.window).filter(window => !window.isDestroyed());
}

function documentOrigin(url: string): string {
  const parsed = new URL(url);
  // Chromium reports a file: document's origin as "file://"; Node's URL says "null".
  return parsed.protocol === "file:" ? "file://" : parsed.origin;
}

/** Called before the window loads the renderer, so its first requests are already accepted. */
export function trustRendererDocument(window: BrowserWindow, documentUrl: string): void {
  const contents = window.webContents;
  rendererWindows.set(contents, { window, origin: documentOrigin(documentUrl), document: documentUrl.split("#", 1)[0] });
  if (typeof window.once === "function") window.once("closed", () => rendererWindows.delete(contents));
}

export function sentByLoomRenderer(event: IpcMainInvokeEvent): boolean {
  const trusted = rendererWindows.get(event.sender);
  const window = trusted?.window;
  if (!window || window.isDestroyed()) return false;
  const contents = window.webContents;
  const frame = event.senderFrame;
  return event.sender === contents && frame !== null && frame === contents.mainFrame
    && frame.origin === trusted!.origin
    && (!frame.url || frame.url.split("#", 1)[0] === trusted!.document);
}

export function fromLoomRenderer<Args extends unknown[], Result>(
  handler: (event: IpcMainInvokeEvent, ...args: Args) => Result,
): (event: IpcMainInvokeEvent, ...args: Args) => Result {
  return (event, ...args) => {
    if (!sentByLoomRenderer(event)) throw new Error("Refused a request that did not come from the Loom window.");
    return handler(event, ...args);
  };
}

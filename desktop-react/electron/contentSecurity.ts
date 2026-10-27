import type { BrowserWindow } from "electron";

/** The document the desktop window shows: the built `dist/index.html`, or the Vite server in development. */
export interface RendererDocument {
  url: string;
  development: boolean;
}

/**
 * What the Loom window may load and run. Everything not named is refused, and no script runs unless it is
 * one of Loom's own files: that is what keeps injected markup, and anything a model or a web page can
 * get into a message, from reaching the `window.loom` bridge.
 *
 * The page cannot carry this itself. index.html is also Loom Web's entry, which loads stylesheets and a
 * wallpaper from smirel.com, renders artifacts from blob: URLs that inherit their creator's policy, and
 * talks to its own origin, so the desktop window gets it as a response header from the main process.
 *
 * A screen that genuinely needs another source gets it added here, and in contentSecurity.test.mjs,
 * which pins the list.
 */
export function rendererContentSecurityPolicy(document: RendererDocument): string {
  const connect = ["'self'", "blob:", "data:"];
  if (document.development) {
    // Vite's hot-reload channel only exists on the dev server.
    const origin = new URL(document.url).origin;
    connect.push(origin, origin.replace(/^http/, "ws"));
  }
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: http: https: loom-artifact:",
    "media-src 'self' data: blob: loom-artifact:",
    "font-src 'self' data:",
    `connect-src ${connect.join(" ")}`,
    "frame-src loom-artifact:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

/**
 * Adds the policy to the window's own document and nothing else: the session is shared with artifact
 * preview windows, which show workspace files that must keep working as they are.
 */
export function installRendererContentSecurity(window: BrowserWindow, document: RendererDocument): void {
  const policy = rendererContentSecurityPolicy(document);
  const target = new URL(document.url);
  const pattern = target.protocol === "file:" ? "file:///*" : `${target.origin}/*`;
  window.webContents.session.webRequest.onHeadersReceived({ urls: [pattern] }, (details, callback) => {
    if (details.resourceType !== "mainFrame" || details.webContentsId !== window.webContents.id || window.isDestroyed()) {
      callback({});
      return;
    }
    let sameDocument = false;
    try {
      const url = new URL(details.url);
      sameDocument = target.protocol === "file:" ? url.protocol === "file:" : url.origin === target.origin;
    } catch {
      // An unparsable address is not Loom's document.
    }
    callback(sameDocument
      ? { responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [policy] } }
      : {});
  });
}

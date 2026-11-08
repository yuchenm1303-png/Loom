import type { BrowserWindow } from "electron";

/** The document the desktop window shows: the built `dist/index.html`, or the Vite server in development. */
export interface RendererDocument {
  url: string;
  development: boolean;
}
const policies = new WeakMap<BrowserWindow["webContents"]["session"], Map<number, { window: BrowserWindow; target: URL; policy: string }>>();

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
  const session = window.webContents.session;
  const rules = policies.get(session) || new Map();
  policies.set(session, rules);
  const contentsId = window.webContents.id;
  rules.set(contentsId, { window, target, policy });
  if (typeof window.once === "function") window.once("closed", () => rules.delete(contentsId));
  const patterns = [...new Set([...rules.values()].map(rule => rule.target.protocol === "file:" ? "file:///*" : `${rule.target.origin}/*`))];
  session.webRequest.onHeadersReceived({ urls: patterns }, (details, callback) => {
    const rule = rules.get(details.webContentsId);
    if (!rule || details.resourceType !== "mainFrame" || rule.window.isDestroyed()) {
      callback({});
      return;
    }
    let sameDocument = false;
    try {
      const url = new URL(details.url);
      sameDocument = url.protocol === rule.target.protocol && url.host === rule.target.host && url.pathname === rule.target.pathname;
    } catch {
      // An unparsable address is not Loom's document.
    }
    callback(sameDocument
      ? { responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [rule.policy] } }
      : {});
  });
}

import type { TranscriptItem } from "../types/loom";

export interface BrowserActivity {
  id: string;
  url: string;
  title: string;
  tabId: string;
  connection: string;
  screenshot: string;
  status: "working" | "waiting" | "failed" | "closed" | "ready";
}
export interface BrowserActivityTarget extends BrowserActivity { workspace?: string }

function record(value: unknown): Record<string, unknown> {
  if (typeof value === "string") {
    try { return record(JSON.parse(value)); } catch { return {}; }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string { return typeof value === "string" ? value.trim() : ""; }
export function browserPageUrl(value: unknown): string {
  const raw = text(value);
  try {
    const url = new URL(raw);
    return /^(https?:)$/.test(url.protocol) && !url.username && !url.password && !raw.includes("[REDACTED]") ? raw : "";
  } catch { return ""; }
}

/** Fold observations into one card per runtime browser, never one card per action. */
export function collectBrowserActivities(items: TranscriptItem[], active: boolean): BrowserActivity[] {
  const sessions = new Map<string, BrowserActivity>();
  for (const item of items) {
    const name = text(item.toolName);
    if (item.type !== "tool_call" || !name.startsWith("browser_")) continue;
    const args = record(item.arguments);
    const result = record(item.result);
    const id = text(result.browser_id) || text(args.browser_id);
    if (!id) continue; // Discovery/status calls are not browser sessions.
    const previous = sessions.get(id);
    const info = record(result.page_info);
    const url = browserPageUrl(result.url);
    const observedTabId = text(info.tab_id) || text(result.tab_id);
    const changedPage = Boolean((url && previous?.url && previous.url !== url)
      || (observedTabId && previous?.tabId && observedTabId !== previous.tabId));
    const screenshot = name === "browser_screenshot" ? text(result.path) : "";
    const status = text(item.status);
    const busy = /^(started|running|streaming|streaming_arguments|pending)$/.test(status);
    const waiting = /^(waiting|waiting_approval)$/.test(status);
    const failed = /^(failed|denied|cancelled|interrupted)$/.test(status) || result.ok === false;
    const closed = name === "browser_close" && /^(completed|success|succeeded)$/.test(status);
    sessions.set(id, {
      id,
      url: url || previous?.url || browserPageUrl(args.url),
      title: text(result.title) || (changedPage ? "" : previous?.title || ""),
      tabId: observedTabId || previous?.tabId || "",
      connection: text(result.browser_connection) || previous?.connection || text(args.connect),
      screenshot: /\.png$/i.test(screenshot) ? screenshot : changedPage ? "" : previous?.screenshot || "",
      status: closed ? "closed" : failed ? "failed" : active && waiting ? "waiting" : active && busy ? "working" : "ready",
    });
  }
  return [...sessions.values()].filter(session => session.url);
}

export function matchingBrowserTab(target: BrowserActivity, tabs: { id: string; url: string }[]): string | null {
  // Only embedded IDs belong to this browser. External IDs can collide.
  if (target.connection !== "embedded") return null;
  if (target.tabId) return tabs.find(tab => tab.id === target.tabId)?.id ?? null;
  return tabs.find(tab => tab.url === target.url)?.id ?? null;
}

/** Image pages are already previews; local artifacts can survive their temporary HTTP server. */
export function browserImagePreview(value: string): { url: string; localPath: string } | null {
  const safe = browserPageUrl(value);
  if (!safe) return null;
  const url = new URL(safe);
  if (!/\.(png|jpe?g|webp|gif|avif)$/i.test(url.pathname)) return null;
  let path = "";
  if (["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
    try {
      const decoded = decodeURIComponent(url.pathname).replace(/^\/+/, "");
      if (decoded.startsWith("artifacts/") && !decoded.split(/[\\/]/).includes("..")) path = decoded;
    } catch { /* Keep the HTTP URL when its path cannot be decoded. */ }
  }
  return { url: safe, localPath: path };
}

export function browserActivityOpenCommand(target: BrowserActivity, tabs: { id: string; url: string }[]):
  { action: "select-tab"; tabId: string } | { action: "new-tab"; url: string } {
  const original = target.status !== "closed" ? matchingBrowserTab(target, tabs) : null;
  const existing = original || tabs.find(tab => tab.url === target.url)?.id;
  return existing ? { action: "select-tab", tabId: existing } : { action: "new-tab", url: target.url };
}

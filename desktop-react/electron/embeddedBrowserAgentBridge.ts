import crypto from "node:crypto";
import type { EmbeddedBrowserManager } from "./embeddedBrowser.js";

const ORIGIN = "http://127.0.0.1:39223";
const BASE = "/browser-extension/v1";
const VERSION = "0.1.19";

/**
 * Dedicated, authenticated loopback CLIENT. Python owns the queue and applies
 * the existing Browser Use security/permission chain. This process may execute
 * commands only against tabs the human explicitly opted in.
 * No CDP debug port is enabled: the main Loom renderer is never inspectable.
 */
export class EmbeddedBrowserAgentClient {
  private stopped = false;
  private readonly clientId = crypto.randomUUID();
  private loop: Promise<void> | null = null;
  private readonly abort = new AbortController();

  constructor(
    private readonly manager: EmbeddedBrowserManager,
    private readonly token: string,
  ) {}

  start(): void {
    if (!this.loop) this.loop = this.work();
  }

  stop(): void {
    this.stopped = true;
    this.abort.abort();
  }

  private async request(endpoint: string, body?: Record<string, unknown>): Promise<Record<string, unknown>> {
    const signal = AbortSignal.any([this.abort.signal, AbortSignal.timeout(34_000)]);
    const response = await fetch(ORIGIN + BASE + endpoint, {
      method: body ? "POST" : "GET",
      headers: {
        "X-Loom-Token": this.token,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal,
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Embedded browser service offline (" + response.status + ")");
    const json: unknown = await response.json();
    if (!json || typeof json !== "object") throw new Error("Invalid browser bridge response.");
    return json as Record<string, unknown>;
  }

  private async register(): Promise<void> {
    const snapshot = this.manager.snapshot();
    const active = snapshot.tabs.find(tab => tab.id === snapshot.activeId);
    await this.request("/register", {
      client_id: this.clientId,
      version: VERSION,
      protocol_version: 1,
      browser: "Loom Embedded",
      active_tab: active ? {
        tab_id: active.id, window_id: "loom", title: active.title, url: active.url,
      } : null,
    });
  }

  private async work(): Promise<void> {
    let registeredAt = 0;
    while (!this.stopped) {
      try {
        if (Date.now() - registeredAt > 12_000) {
          await this.register();
          registeredAt = Date.now();
        }
        const endpoint = "/poll?client_id=" + encodeURIComponent(this.clientId)
          + "&version=" + VERSION + "&browser=Loom%20Embedded";
        const payload = await this.request(endpoint);
        const command = payload.command as { id?: unknown; action?: unknown; args?: unknown } | null;
        if (!command) continue;
        const id = String(command.id || "");
        const action = String(command.action || "");
        const args = command.args && typeof command.args === "object" && !Array.isArray(command.args)
          ? command.args as Record<string, unknown> : {};
        if (!id || !action) continue;
        try {
          const result = await this.manager.agentCommand(action, args);
          await this.request("/result", { id, ok:true, result });
        } catch (cause) {
          const error = cause instanceof Error ? cause.message : String(cause);
          await this.request("/result", { id, ok:false, error }).catch(() => {});
        }
      } catch {
        // Loom Host may not have started its Python browser endpoint yet.
        // Never fall back to another user's browser or open a debug port.
        if (!this.stopped) {
          await new Promise(resolve => setTimeout(resolve, 1500));
          registeredAt = 0;
        }
      }
    }
  }
}

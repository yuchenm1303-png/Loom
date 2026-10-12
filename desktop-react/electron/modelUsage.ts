import crypto from "node:crypto";
import type { ModelLaunchSpec } from "./modelManager.js";
import type { ModelUsage, UsageWindow } from "../src/types/modelUsage.js";

function numeric(value: unknown): number | undefined {
  if (typeof value !== "number" && typeof value !== "string") return;
  if (typeof value === "string" && !value.trim()) return;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

export function usageEndpoint(baseUrl: string): { provider: string; url: string } | null {
  try {
    const url = new URL(baseUrl);
    if (url.protocol !== "https:" || url.port || url.username || url.password) return null;
    if (url.hostname === "api.deepseek.com") return { provider: "DeepSeek", url: "https://api.deepseek.com/user/balance" };
    if (url.hostname === "api.minimaxi.com") return { provider: "MiniMax", url: "https://www.minimaxi.com/v1/token_plan/remains" };
    if (url.hostname === "api.minimax.io") return { provider: "MiniMax", url: "https://www.minimax.io/v1/token_plan/remains" };
  } catch {}
  return null;
}

export function parseModelUsage(provider: string, payload: any, model: string, now = Date.now()): ModelUsage {
  const result: ModelUsage = { status: "ok", provider, checkedAt: now, windows: [], balances: [] };
  if (provider === "DeepSeek") {
    if (!Array.isArray(payload?.balance_infos)) throw new Error("Invalid balance response");
    for (const row of payload.balance_infos) {
      if (typeof row.currency !== "string" || numeric(row.total_balance) === undefined) continue;
      result.balances.push({ currency: row.currency, total: String(row.total_balance),
        toppedUp: numeric(row.topped_up_balance) === undefined ? undefined : String(row.topped_up_balance),
        granted: numeric(row.granted_balance) === undefined ? undefined : String(row.granted_balance) });
    }
    if (!result.balances.length) throw new Error("Missing balance");
    return result;
  }
  if (payload?.base_resp?.status_code != null && Number(payload.base_resp.status_code) !== 0) throw new Error("Provider rejected quota request");
  const data = payload?.data ?? payload;
  const rows = Array.isArray(data?.model_remains) ? data.model_remains : [];
  const row = rows.find((item: any) => item.model_name === model)
    ?? (/^MiniMax-M/i.test(model) ? rows.find((item: any) => item.model_name === "general") : undefined);
  if (!row) throw new Error("Missing quota for selected model");
  for (const [prefix, label, timeField, durationField] of [
    ["current_interval", "5h", "end_time", "remains_time"],
    ["current_weekly", "Weekly", "weekly_end_time", "weekly_remains_time"],
  ]) {
    // Status 3 means the model class is not included, even when percent says 100.
    if (Number(row[`${prefix}_status`]) === 3) continue;
    let percent = numeric(row[`${prefix}_remaining_percent`]);
    const total = numeric(row[`${prefix}_total_count`]);
    const used = numeric(row[`${prefix}_usage_count`]);
    if (percent === undefined && total !== undefined && total > 0 && used !== undefined) percent = 100 * (total - used) / total;
    if (percent === undefined) continue;
    const end = numeric(row[timeField]);
    const duration = numeric(row[durationField]);
    const window: UsageWindow = { label, remainingPercent: Math.max(0, Math.min(100, percent)) };
    if (end !== undefined && end > 0) window.resetsAt = end < 1e12 ? end * 1000 : end;
    else if (duration !== undefined && duration >= 0) window.resetsAt = now + duration;
    result.windows.push(window);
  }
  if (!result.windows.length) throw new Error("No included quota windows");
  return result;
}

export class ModelUsageService {
  private cache = new Map<string, { payload?: unknown; checkedAt: number }>();
  private pending = new Map<string, Promise<{ payload?: unknown; checkedAt: number }>>();
  constructor(private fetcher: typeof fetch = fetch) {}

  async query(spec: ModelLaunchSpec, force = false): Promise<ModelUsage> {
    const target = usageEndpoint(spec.baseUrl);
    const empty = (status: ModelUsage["status"], message: string): ModelUsage => ({
      status, provider: target?.provider ?? "", checkedAt: Date.now(), windows: [], balances: [], message,
    });
    if (!target || spec.authMode === "loom-account") return empty("unsupported", "此连接暂不支持额度查询");
    if (!spec.apiKey) return empty("unconfigured", "请先配置此连接的 API Key");
    // Hash credentials: cache entries never contain a key and key edits cannot
    // reuse the previous account's balance. No credential reaches the renderer.
    const key = crypto.createHash("sha256").update(`${target.url}\0${spec.apiKey}`).digest("hex");
    const interpret = (raw: { payload?: unknown; checkedAt: number }): ModelUsage => {
      try { return parseModelUsage(target.provider, raw.payload, spec.model, raw.checkedAt); }
      catch { return empty("error", "查询失败，请稍后刷新；请确认 Key 类型及所属平台"); }
    };
    const pending = this.pending.get(key);
    if (pending) return interpret(await pending);
    const cached = this.cache.get(key);
    if (!force && cached && Date.now() - cached.checkedAt < (cached.payload !== undefined ? 60_000 : 15_000)) return interpret(cached);
    const request = (async () => {
      let payload: unknown;
      try {
        const response = await this.fetcher(target.url, {
          headers: { Authorization: `Bearer ${spec.apiKey}` },
          signal: AbortSignal.timeout(8_000), redirect: "error",
        });
        if (!response.ok) throw new Error("Provider request failed");
        // Bound the body as well as the request time, without loading an
        // arbitrarily large provider response into the Host process.
        if (!response.body) throw new Error("Missing body");
        const reader = response.body.getReader();
        const chunks: Uint8Array[] = [];
        let size = 0;
        try {
          for (;;) {
            const chunk = await reader.read();
            if (chunk.done) break;
            size += chunk.value.length;
            if (size > 65536) throw new Error("Quota response too large");
            chunks.push(chunk.value);
          }
        } finally { await reader.cancel().catch(() => {}); }
        payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        // Never forward response bodies, URLs, headers or exceptions containing keys.
      }
      const result = { payload, checkedAt: Date.now() };
      this.cache.delete(key);
      this.cache.set(key, result);
      if (this.cache.size > 64) this.cache.delete(this.cache.keys().next().value!);
      return result;
    })().finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return interpret(await request);
  }
}

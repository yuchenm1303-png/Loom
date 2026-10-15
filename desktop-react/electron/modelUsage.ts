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
    if (url.hostname === "opencode.ai" && url.pathname.replace(/\/$/, "") === "/zen/go/v1") return { provider: "OpenCode Go", url: "https://opencode.ai/zen/go/v1/usage" };
    if (url.hostname === "muxway.dev") return { provider: "Muxway Relay", url: "https://muxway.dev/api/usage/token/" };
  } catch {}
  return null;
}

export function accountUsageEndpoint(spec: ModelLaunchSpec): string | null {
  if (spec.authMode !== "loom-account" || spec.kind === "saved") return null;
  try {
    const root = new URL(process.env.LOOM_MODEL_GATEWAY_BASE_URL || "https://account.smirel.com/model/v1");
    if (root.username || root.password || root.search || root.hash
      || (root.protocol !== "https:" && !(root.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(root.hostname)))) return null;
    const base = spec.baseUrl.replace(/\/$/, "");
    const prefix = root.href.replace(/\/$/, "");
    if (!["", "/minimax", "/deepseek", "/intern", "/opencode-go", "/managed-relay"].some(suffix => base === prefix + suffix)) return null;
    return `${base}/usage?model=${encodeURIComponent(spec.model)}`;
  } catch { return null; }
}

function normalizedUsage(payload: any): ModelUsage {
  if (!payload || !["ok", "error", "unsupported", "unconfigured"].includes(payload.status)
    || !Array.isArray(payload.windows) || !Array.isArray(payload.balances) || !Number.isFinite(payload.checkedAt)) throw Error("Invalid usage snapshot");
  return { status: payload.status, provider: String(payload.provider || "").slice(0, 80), checkedAt: payload.checkedAt,
    windows: payload.windows.filter((r: any) => r && typeof r.label === "string" && numeric(r.remainingPercent) !== undefined)
      .map((r: any) => ({ label: r.label.slice(0, 80), remainingPercent: Math.max(0, Math.min(100, Number(r.remainingPercent))),
        ...(numeric(r.resetsAt) !== undefined ? { resetsAt: Number(r.resetsAt) } : {}) })),
    balances: payload.balances.filter((r: any) => r && typeof r.currency === "string" && numeric(r.total) !== undefined)
      .map((r: any) => ({ currency: r.currency.slice(0, 32), total: String(r.total),
        ...(numeric(r.toppedUp) !== undefined ? { toppedUp: String(r.toppedUp) } : {}),
        ...(numeric(r.granted) !== undefined ? { granted: String(r.granted) } : {}) })),
    message: typeof payload.message === "string" ? payload.message.slice(0, 600) : undefined,
    code: typeof payload.code === "string" ? payload.code.slice(0, 80) : undefined,
    scope: typeof payload.scope === "string" ? payload.scope.slice(0, 80) : undefined };
}

export function parseModelUsage(provider: string, payload: any, model: string, now = Date.now()): ModelUsage {
  const result: ModelUsage = { status: "ok", provider, checkedAt: now, windows: [], balances: [] };
  result.scope = "provider-account";
  if (provider === "OpenCode Go") {
    if (!payload?.usage || typeof payload.usage !== "object") throw Error("Missing Go usage");
    for (const [key, label] of [["rolling", "5h"], ["weekly", "Weekly"], ["monthly", "Monthly"]]) {
      const row = payload.usage[key];
      const used = numeric(row?.percent);
      if (used === undefined) continue;
      const reset = typeof row.resetsAt === "string" ? Date.parse(row.resetsAt) : NaN;
      result.windows.push({ label, remainingPercent: Math.max(0, Math.min(100, 100 - used)),
        ...(Number.isFinite(reset) ? { resetsAt: reset } : {}) });
    }
    if (!result.windows.length) throw Error("Go did not report quota windows");
    return result;
  }
  if (provider === "Muxway Relay") {
    const data = payload?.data;
    if (!data || payload.success === false || (payload.code != null && Number(payload.code) !== 0)) throw Error("Missing token usage");
    result.scope = "api-key";
    if (data.unlimited_quota === true) { result.message = "此 Key 未设置独立额度上限；不代表上游账号余额无限。"; return result; }
    if (numeric(data.total_available) === undefined) throw Error("Missing token quota");
    result.balances.push({ currency: "额度单位", total: String(data.total_available) });
    return result;
  }
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
  private cache = new Map<string, { payload?: unknown; checkedAt: number; failureCode?: string }>();
  private pending = new Map<string, Promise<{ payload?: unknown; checkedAt: number; failureCode?: string }>>();
  constructor(private fetcher: typeof fetch = fetch) {}

  async query(spec: ModelLaunchSpec, force = false, accountToken?: string): Promise<ModelUsage> {
    const account = spec.authMode === "loom-account";
    const accountUrl = accountUsageEndpoint(spec);
    const target = account ? (accountUrl ? { provider: "Loom", url: accountUrl } : null) : usageEndpoint(spec.baseUrl);
    const credential = account ? accountToken : spec.apiKey;
    const empty = (status: ModelUsage["status"], message: string): ModelUsage => ({
      status, provider: target?.provider ?? "", checkedAt: Date.now(), windows: [], balances: [], message,
    });
    if (!target) {
      const result = empty("unsupported", "此供应商尚无已验证的额度查询接口，请在其控制台查看；这不代表没有额度。");
      if (!account) {
        const host = (() => { try { return new URL(spec.baseUrl).hostname; } catch { return ""; } })();
        if (host === "discovery-api.intern-ai.org.cn") {
          result.provider = "Intern Discovery";
          result.dashboardUrl = "https://discovery.intern-ai.org.cn/token-plan/home";
          result.message = "Intern 余额接口需要控制台登录态，API Key 不能直接读取；请在供应商控制台查看。";
        } else if (host === "api.ant-ling.com") {
          result.provider = "Ant Ling";
          result.dashboardUrl = "https://chat.ant-ling.com";
        }
      }
      return result;
    }
    if (!credential) return empty("unconfigured", account ? "请先登录 Loom 以查询内置模型额度" : "请先配置此连接的 API Key");
    // Hash credentials: cache entries never contain a key and key edits cannot
    // reuse the previous account's balance. No credential reaches the renderer.
    const key = crypto.createHash("sha256").update(`${target.url}\0${credential}`).digest("hex");
    const interpret = (raw: { payload?: unknown; checkedAt: number; failureCode?: string }): ModelUsage => {
      if (raw.failureCode) return { ...empty("error", `额度查询暂时失败（${raw.failureCode}），不代表额度为零。`), code: raw.failureCode };
      try { return account ? normalizedUsage(raw.payload) : parseModelUsage(target.provider, raw.payload, spec.model, raw.checkedAt); }
      catch { return empty("error", "查询失败，请稍后刷新；请确认 Key 类型及所属平台"); }
    };
    const pending = this.pending.get(key);
    if (pending) return interpret(await pending);
    const cached = this.cache.get(key);
    if (!force && cached && Date.now() - cached.checkedAt < (cached.payload !== undefined ? 60_000 : 15_000)) return interpret(cached);
    const request = (async () => {
      let payload: unknown;
      let failureCode: string | undefined;
      try {
        const response = await this.fetcher(target.url + (account && force ? "&force=1" : ""), {
          headers: { Authorization: `Bearer ${credential}`, Accept: "application/json", "User-Agent": "Loom/0.1 (coding-agent)" },
          signal: AbortSignal.timeout(account ? 20_000 : 8_000), redirect: "error",
        });
        if (!response.ok) { failureCode = `HTTP_${response.status}`; throw new Error("Provider request failed"); }
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
        failureCode ??= "NETWORK_OR_RESPONSE_ERROR";
      }
      const result = { payload, checkedAt: Date.now(), failureCode };
      this.cache.delete(key);
      this.cache.set(key, result);
      if (this.cache.size > 64) this.cache.delete(this.cache.keys().next().value!);
      return result;
    })().finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return interpret(await request);
  }
}

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { ExternalLink, RefreshCw } from "./icons";
import type { ModelUsage } from "../types/modelUsage";
import { ComposerPanel, PanelFoot, PanelHead, useComposerPanel } from "./composerPanel";

const ZH_WINDOWS: Record<string, string> = { Weekly: "每周", Monthly: "每月", "5h": "5 小时" };
const EN_WINDOWS: Record<string, string> = { "5h": "5 hours" };

/** "Resets in 2 h 24 min" within a day, "Resets Sat 10:17" after that; the exact time is in the tooltip. */
function resetText(at: number, zh: boolean): string {
  const left = at - Date.now();
  if (left > 0 && left < 86_400_000) {
    const total = Math.max(1, Math.round(left / 60_000));
    const hours = Math.floor(total / 60);
    const minutes = total % 60;
    if (zh) return hours ? `${hours} 小时${minutes ? ` ${minutes} 分钟` : ""}后重置` : `${minutes} 分钟后重置`;
    return hours ? `Resets in ${hours} h${minutes ? ` ${minutes} min` : ""}` : `Resets in ${minutes} min`;
  }
  const when = new Date(at).toLocaleString(zh ? "zh-CN" : "en-US", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  return zh ? `${when} 重置` : `Resets ${when}`;
}

/**
 * The quota ring beside the model chip, and the panel it opens: what is left of each window of the plan behind the
 * current model, or the money left on its account. One of the composer's panels (composerPanel.tsx).
 */
export function ModelUsageIndicator({ selection, zh }: { selection: string; zh: boolean }) {
  const panel = useComposerPanel("usage");
  const { close } = panel;
  const [state, setState] = useState<{ selection: string; data?: ModelUsage; loading: boolean }>({ selection, loading: false });
  const requestId = useRef(0);
  const refresh = useCallback(async (force = false) => {
    const id = ++requestId.current;
    setState(previous => ({ selection, data: previous.selection === selection ? previous.data : undefined, loading: true }));
    let data: ModelUsage;
    try {
      const response: unknown = window.loom?.queryModelUsage
        ? await window.loom.queryModelUsage(selection, force)
        : null;
      // Older Hosts and test bridges may return an empty object instead of a
      // complete quota snapshot. Never let a partial response crash Composer.
      data = response && typeof response === "object"
        && Array.isArray((response as ModelUsage).windows)
        && Array.isArray((response as ModelUsage).balances)
        ? response as ModelUsage
        : { status: "unsupported", provider: "", windows: [], balances: [], checkedAt: Date.now(), message: "请更新本地 Host 以启用额度查询" };
    } catch {
      data = { status: "error", provider: "", windows: [], balances: [], checkedAt: Date.now(), message: "无法连接额度查询服务，请稍后刷新" };
    }
    if (id !== requestId.current) return;
    setState(previous => ({ selection, loading: false, data:
      data.status === "error" && !["MODEL_NOT_ENTITLED", "ACCOUNT_DISABLED", "INVALID_MODEL_TOKEN", "HTTP_401", "HTTP_403"].includes(data.code || "")
        && previous.selection === selection && previous.data?.status === "ok" && Date.now() - previous.data.checkedAt < 900_000
        ? { ...previous.data, stale: true, message: data.message, code: data.code } : data }));
  }, [selection]);

  useEffect(() => {
    close();
    if (selection) void refresh();
    const timer = window.setInterval(() => { if (selection && document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => { window.clearInterval(timer); ++requestId.current; };
  }, [selection, refresh, close]);

  const data = state.selection === selection ? state.data : undefined;
  const loading = state.selection === selection && state.loading;
  // The tightest window limits the account, even if its 5-hour meter is full.
  const percent = data?.windows?.length ? Math.min(...data.windows.map(row => row.remainingPercent)) : undefined;
  const title = zh ? "余额与额度" : "Balance & quota";
  const detail = percent !== undefined ? `${percent}% ${zh ? "剩余" : "remaining"}` : data?.balances?.map(row => `${row.currency} ${row.total}`).join(" · ");
  const scope = data?.scope
    ? data.scope.startsWith("loom-shared-")
      ? (zh ? "Loom 上游共享额度，多个模型及账号可能共用；不是你的独立配额。" : "Shared Loom upstream quota; not a separate allowance for this user or model.")
      : data.scope === "api-key" ? (zh ? "当前 API Key 的额度，不代表整个账号余额。" : "This API key's allowance, not the account balance.")
      : (zh ? "供应商账号额度，同账号下的模型和设备可能共享。" : "Provider account allowance, potentially shared across models and devices.")
    : "";
  return (
    <div ref={panel.anchorRef} className="composer-control-anchor model-usage-anchor cp-anchor">
      <button ref={panel.triggerRef} type="button" className={`model-usage-button ${loading && !data ? "is-loading" : ""}`}
        title={`${title}${detail ? ` · ${detail}` : ""}`} aria-label={`${title}${detail ? ` · ${detail}` : ""}`}
        aria-expanded={panel.open} aria-haspopup="dialog" disabled={!selection} onClick={panel.toggle}>
        <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true">
          <circle className="model-usage-track" cx="10" cy="10" r="7" />
          {percent !== undefined ? <circle className={`model-usage-value ${percent <= 20 ? "is-low" : ""}`} cx="10" cy="10" r="7"
            pathLength="100" strokeDasharray={`${percent} 100`} transform="rotate(-90 10 10)" /> : null}
        </svg>
      </button>
      <ComposerPanel panel={panel} label={title} size="s" className="model-usage-popover">
        <PanelHead title={title} subtitle={data?.provider || undefined}
          aside={<button type="button" className="cp-icon" aria-label={zh ? "刷新" : "Refresh"} title={zh ? "刷新" : "Refresh"}
            disabled={loading} onClick={() => void refresh(true)}><RefreshCw size={14} spinning={loading} /></button>} />
        <div className="cp-body">
          {!data ? <div className="cp-empty" role="status">{zh ? "正在查询…" : "Loading…"}</div> : null}
          {data?.windows?.map(row => <div className="cp-usage-window model-usage-window" key={row.label}>
            <div className="cp-usage-line">
              <span>{zh ? ZH_WINDOWS[row.label] || row.label : EN_WINDOWS[row.label] || row.label}</span>
              <strong>{row.remainingPercent}% {zh ? "剩余" : "left"}</strong>
            </div>
            <div className={`cp-meter ${row.remainingPercent <= 20 ? "is-low" : ""}`} role="meter" aria-label={row.label}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={row.remainingPercent}
              style={{ "--cp-value": row.remainingPercent } as CSSProperties}><i /></div>
            {row.resetsAt ? <small title={new Date(row.resetsAt).toLocaleString(zh ? "zh-CN" : undefined)}>{resetText(row.resetsAt, zh)}</small> : null}
          </div>)}
          {data?.balances?.map(row => <div className="cp-usage-balance model-usage-balance" key={row.currency}>
            <div className="cp-usage-line"><span>{zh ? "可用余额" : "Available"}</span><strong>{row.currency} {row.total}</strong></div>
            {row.toppedUp !== undefined || row.granted !== undefined ? <small>
              {[row.toppedUp !== undefined ? `${zh ? "充值余额" : "Paid"} ${row.toppedUp}` : "", row.granted !== undefined ? `${zh ? "赠送余额" : "Granted"} ${row.granted}` : ""]
                .filter(Boolean).join(" · ")}
            </small> : null}
          </div>)}
          {scope ? <p className="cp-usage-scope">{scope}</p> : null}
          {data?.message ? <div className={`cp-note ${data.status === "error" ? "is-error" : ""}`} role="status">{data.message}</div> : null}
        </div>
        {data && (data.status === "ok" || data.dashboardUrl) ? <PanelFoot className="cp-usage-foot">
          <span>
            {data.status === "ok" ? <span className="model-usage-updated">{data.stale ? (zh ? "上次成功查询：" : "Last successful check: ") : (zh ? "更新于 " : "Updated ")}{new Date(data.checkedAt).toLocaleTimeString(zh ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit", hour12: false })} · {zh ? "以供应商数据为准" : "provider reported"}</span> : null}
            {data.dashboardUrl ? <> <button type="button" className="cp-link" onClick={() => void window.loom.openExternal(data.dashboardUrl!)}>
              {zh ? "打开供应商控制台" : "Open provider console"}<ExternalLink size={12} strokeWidth={1.75} aria-hidden="true" /></button></> : null}
          </span>
        </PanelFoot> : null}
      </ComposerPanel>
    </div>
  );
}

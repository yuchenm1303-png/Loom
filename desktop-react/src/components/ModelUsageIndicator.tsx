import { useCallback, useEffect, useRef, useState } from "react";
import type { ModelUsage } from "../types/modelUsage";
import "./model-usage.css";

const cached = new Map<string, ModelUsage>();

export function ModelUsageIndicator({ selection, zh, onOpen }: { selection: string; zh: boolean; onOpen(): void }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ selection: string; data?: ModelUsage; loading: boolean; error?: string }>({ selection, data: cached.get(selection), loading: false });
  const root = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const refresh = useCallback(async (force = false) => {
    const id = ++requestId.current;
    setState(previous => ({ selection, data: previous.selection === selection ? previous.data : cached.get(selection), loading: true }));
    let data: ModelUsage;
    try {
      data = window.loom?.queryModelUsage
        ? await window.loom.queryModelUsage(selection, force)
        : { status: "unsupported", provider: "", windows: [], balances: [], checkedAt: Date.now(), message: "请更新本地 Host 以启用额度查询" };
    } catch {
      data = { status: "error", provider: "", windows: [], balances: [], checkedAt: Date.now(), message: "无法连接额度查询服务，请稍后刷新" };
    }
    if (id !== requestId.current) return;
    if (data.status === "ok") {
      cached.delete(selection);
      cached.set(selection, data);
      if (cached.size > 64) cached.delete(cached.keys().next().value!);
    }
    setState(previous => ({ selection, loading: false,
      data: data.status === "error" && previous.selection === selection && previous.data?.status === "ok" ? previous.data : data,
      error: data.status === "error" ? data.message : undefined }));
  }, [selection]);

  useEffect(() => {
    setOpen(false);
    if (selection) void refresh();
    const timer = window.setInterval(() => { if (selection && document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => { window.clearInterval(timer); ++requestId.current; };
  }, [selection, refresh]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  const data = state.selection === selection ? state.data : cached.get(selection);
  const loading = state.selection === selection && state.loading;
  const percent = data?.windows[0]?.remainingPercent;
  const title = zh ? "余额与额度" : "Balance & quota";
  const detail = percent !== undefined ? `${percent}% ${zh ? "剩余" : "remaining"}` : data?.balances.map(row => `${row.currency} ${row.total}`).join(" · ");
  return (
    <div ref={root} className="composer-control-anchor model-usage-anchor">
      <button type="button" className={`model-usage-button ${loading && !data ? "is-loading" : ""}`}
        title={`${title}${detail ? ` · ${detail}` : ""}`} aria-label={`${title}${detail ? ` · ${detail}` : ""}`}
        aria-expanded={open} aria-haspopup="dialog" disabled={!selection}
        onClick={() => { if (!open) onOpen(); setOpen(value => !value); }}>
        <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
          <circle className="model-usage-track" cx="10" cy="10" r="7" />
          {percent !== undefined ? <circle className={`model-usage-value ${percent <= 20 ? "is-low" : ""}`} cx="10" cy="10" r="7"
            pathLength="100" strokeDasharray={`${percent} 100`} transform="rotate(-90 10 10)" /> : null}
          {data?.balances.length ? <text x="10" y="13" textAnchor="middle">{data.balances[0].currency === "CNY" ? "¥" : "$"}</text> : null}
          {data && data.status !== "ok" ? <text x="10" y="13" textAnchor="middle">–</text> : null}
        </svg>
      </button>
      {open ? <div className="composer-popover model-usage-popover" role="dialog" aria-label={title}>
        <div className="model-usage-heading"><strong>{title}</strong><button type="button" disabled={loading} onClick={() => void refresh(true)}>{loading ? (zh ? "查询中…" : "Loading…") : (zh ? "刷新" : "Refresh")}</button></div>
        {data?.provider ? <small>{data.provider}</small> : null}
        {!data ? <p role="status">{zh ? "正在查询…" : "Loading…"}</p> : null}
        {data?.windows.map(row => <div className="model-usage-window" key={row.label}>
          <div><span>{row.label === "Weekly" && zh ? "每周" : row.label}</span><strong>{row.remainingPercent}% {zh ? "剩余" : "left"}</strong></div>
          <progress max={100} value={row.remainingPercent} aria-label={row.label} />
          {row.resetsAt ? <small>{zh ? "重置时间：" : "Resets: "}{new Date(row.resetsAt).toLocaleString(zh ? "zh-CN" : undefined)}</small> : null}
        </div>)}
        {data?.balances.map(row => <div className="model-usage-balance" key={row.currency}>
          <div><span>{zh ? "可用余额" : "Available"}</span><strong>{row.currency} {row.total}</strong></div>
          {row.toppedUp !== undefined ? <small>{zh ? "充值余额" : "Paid"} {row.toppedUp}</small> : null}
          {row.granted !== undefined ? <small>{zh ? "赠送余额" : "Granted"} {row.granted}</small> : null}
        </div>)}
        {data?.message ? <p role="status">{data.message}</p> : null}
        {state.selection === selection && state.error && data?.status === "ok" ? <p role="status">{state.error}（{zh ? "显示上次结果" : "showing cached data"}）</p> : null}
        {data?.status === "ok" ? <small className="model-usage-updated">{zh ? "更新于 " : "Updated "}{new Date(data.checkedAt).toLocaleTimeString()} · {zh ? "以供应商数据为准" : "Provider reported"}</small> : null}
      </div> : null}
    </div>
  );
}

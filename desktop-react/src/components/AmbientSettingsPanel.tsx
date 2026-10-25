import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import "./ambient-settings.css";

type Task = { id?: string; name: string; threadId: string; prompt: string; kind: string; enabled: boolean;
  nextAt?: number | null; seconds: number; path: string; maxRuns: number; maxTokens: number; runsUsed?: number; tokensUsed?: number };
type Run = { id: string; task: string; status: string; result?: string; error?: string; tokens?: number; source: string; turnId?: string };
type Snapshot = { online: boolean; paused: boolean; error?: string; tasks: Task[]; runs: Run[];
  notifications: { id: string; text: string; status: string }[] };
const fresh = (): Task => ({ name: "", threadId: "", prompt: "", kind: "once", enabled: true,
  nextAt: Date.now() / 1000 + 60, seconds: 3600, path: "", maxRuns: 100, maxTokens: 100000 });

export function AmbientSettingsPanel({ threadId }: { threadId?: string | null }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [data, setData] = useState<Snapshot | null>(null);
  const [draft, setDraft] = useState<Task>(() => ({ ...fresh(), threadId: threadId || "" }));
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Run[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [threads, setThreads] = useState<{ id: string; title?: string }[]>([]);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const result = await window.loom.call<Snapshot>("ambient/list", {});
        if (!disposed) { setData(result); setConnectionError(""); }
      } catch (e) { if (!disposed) { setData(null); setConnectionError(String(e)); } }
    };
    void load();
    void window.loom.call<{ threads?: { id: string; title?: string }[] }>("thread/list", {}).then(r => {
      if (!disposed) setThreads(r.threads || []);
    }).catch(() => {});
    const timer = setInterval(() => void load(), 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, []);
  async function mutate(method: string, params: Record<string, unknown>, selectTask = false) {
    setBusy(true);
    try {
      const result = await window.loom.call<{ task?: Task }>(method, params);
      if (selectTask && result.task) setDraft(result.task);
      setData(await window.loom.call<Snapshot>("ambient/list", {}));
      setError("");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  const time = (epoch?: number | null) => epoch == null ? "—" : new Date(epoch * 1000).toLocaleString();
  async function loadHistory() {
    setBusy(true);
    try {
      const result = await window.loom.call<{ runs: Run[]; nextCursor: number | null }>("ambient/history", { before: cursor });
      setHistory(old => [...old, ...result.runs.filter(run => !old.some(previous => previous.id === run.id))]);
      setCursor(result.nextCursor);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <section className="ambient-panel settings-section">
    <div className="settings-page-heading"><div><h1>{zh ? "常驻助理" : "Ambient assistant"}</h1>
      <p>{zh ? "有事件才执行；空闲不调用模型。关闭聊天窗口后由本机 Host 继续等待。" : "Runs on triggers; idle monitoring makes no model calls. Your local Host keeps waiting after chat windows close."}</p></div></div>
    <div className="settings-card ambient-toolbar"><strong>{data?.online ? (zh ? "后台在线" : "Host online") : (zh ? "未连接" : "Not connected")}</strong>
      {data ? <button disabled={busy} onClick={() => void mutate("ambient/pause", { paused: !data.paused })}>{data.paused ? (zh ? "恢复常驻" : "Resume") : (zh ? "暂停常驻" : "Pause")}</button> : null}
      <span>{data?.paused ? (zh ? "已暂停新执行" : "New runs paused") : (zh ? "正在等待触发" : "Waiting for triggers")}</span></div>
    {(error || connectionError || data?.error) && <p role="alert">{error || connectionError || data?.error}</p>}
    <div className="settings-card ambient-form">
      <h2>{draft.id ? (zh ? "编辑任务" : "Edit task") : (zh ? "创建任务" : "Create task")}</h2>
      <label>{zh ? "名称" : "Name"}<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
      <label>{zh ? "会话" : "Thread"}<select value={draft.threadId} onChange={e => setDraft({ ...draft, threadId: e.target.value })}><option value="">{zh ? "选择会话" : "Choose a thread"}</option>
        {threads.map(t => <option key={t.id} value={t.id}>{t.title || t.id}</option>)}
        {draft.threadId && !threads.some(t => t.id === draft.threadId) && <option value={draft.threadId}>{draft.threadId}</option>}</select></label>
      <label>{zh ? "执行指令" : "Instructions"}<textarea rows={3} value={draft.prompt} onChange={e => setDraft({ ...draft, prompt: e.target.value })} /></label>
      <label>{zh ? "触发方式" : "Trigger"}<select value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value, nextAt: draft.nextAt ?? Date.now() / 1000 + 60 })}>
        <option value="once">{zh ? "一次性定时" : "Once"}</option><option value="interval">{zh ? "周期定时" : "Interval"}</option>
        <option value="file">{zh ? "文件变化" : "File changes"}</option><option value="event">{zh ? "适配器事件（未连接外部平台）" : "Adapter event (external platforms unconnected)"}</option></select></label>
      {(draft.kind === "once" || draft.kind === "interval") && <label>{zh ? "下次执行（本地时间）" : "Next run (local time)"}<input type="datetime-local"
        value={draft.nextAt == null ? "" : new Date(draft.nextAt * 1000 - new Date(draft.nextAt * 1000).getTimezoneOffset() * 60000).toISOString().slice(0, 19)} step="1"
        onChange={e => { const epoch = new Date(e.target.value).getTime() / 1000; if (Number.isFinite(epoch)) setDraft({ ...draft, nextAt: epoch }); }} /></label>}
      {draft.kind === "interval" && <label>{zh ? "间隔秒数" : "Interval seconds"}<input type="number" min="1" value={draft.seconds} onChange={e => setDraft({ ...draft, seconds: Number(e.target.value) })} /></label>}
      {draft.kind === "file" && <label>{zh ? "工作区内文件路径" : "Workspace file path"}<input value={draft.path} onChange={e => setDraft({ ...draft, path: e.target.value })} /></label>}
      <label>{zh ? "自动执行次数上限" : "Automatic turn limit"}<input type="number" min="1" value={draft.maxRuns} onChange={e => setDraft({ ...draft, maxRuns: Number(e.target.value) })} /></label>
      <label>{zh ? "累计 token 上限（每轮前检查）" : "Cumulative token limit (checked between turns)"}<input type="number" min="1" value={draft.maxTokens} onChange={e => setDraft({ ...draft, maxTokens: Number(e.target.value) })} /></label>
      <label><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />{zh ? "启用" : "Enabled"}</label>
      <div className="ambient-toolbar"><button disabled={busy || !data || !draft.name || !draft.threadId || !draft.prompt} onClick={() => void mutate("ambient/put", draft, true)}>{zh ? "保存任务" : "Save task"}</button>
        <button onClick={() => setDraft({ ...fresh(), threadId: threadId || "" })}>{zh ? "新建" : "New"}</button></div>
    </div>
    {data?.tasks.map(task => <article className="settings-card ambient-task" key={task.id}>
      <h3>{task.name} · {task.enabled ? (zh ? "已启用" : "Enabled") : (zh ? "已禁用" : "Disabled")}</h3>
      <p>{task.kind} · {zh ? "下次" : "Next"}: {time(task.kind === "once" || task.kind === "interval" ? task.nextAt : null)} · {task.runsUsed}/{task.maxRuns} turns · {task.tokensUsed}/{task.maxTokens} tokens</p>
      <div className="ambient-toolbar"><button onClick={() => setDraft({ ...task, nextAt: task.nextAt ?? Date.now() / 1000 + 60 })}>{zh ? "编辑" : "Edit"}</button>
        <button disabled={busy} onClick={() => void mutate("ambient/put", { id: task.id, enabled: !task.enabled, ...(task.kind === "once" && task.nextAt == null ? { nextAt: Date.now() / 1000 + 60 } : {}) })}>{task.enabled ? (zh ? "禁用" : "Disable") : (zh ? "启用" : "Enable")}</button>
        <button disabled={busy} onClick={() => void mutate("ambient/cancel", { id: task.id })}>{zh ? "停止并禁用" : "Stop and disable"}</button>
        <button disabled={busy} onClick={() => void mutate("ambient/delete", { id: task.id })}>{zh ? "删除" : "Delete"}</button></div>
      {data.runs.filter(r => r.task === task.id).slice(-3).reverse().map(run => <p key={run.id}>{run.status} · {run.source} · {run.tokens ?? 0} tokens<br />{run.error || run.result || run.turnId}</p>)}
    </article>)}
    <details><summary>{zh ? "本地通知与结果" : "Local notifications and results"} ({data?.notifications.length ?? 0})</summary>
      {data?.notifications.slice().reverse().map(n => <p key={n.id}>{n.status}: {n.text}</p>)}</details>
    <details><summary>{zh ? "所有自动执行记录" : "Automatic run history"}</summary>
      {history.map(run => <p key={run.id}>{run.id} · {run.status} · {run.source} · {run.tokens ?? 0} tokens<br />{run.error || run.result || run.turnId}</p>)}
      <button disabled={busy || cursor === null} onClick={() => void loadHistory()}>{zh ? "加载更多记录" : "Load more history"}</button></details>
    <p>{zh ? "通知保存在本地，不会发送外部消息。睡眠和注销期间无法执行；重启后未知结果需人工核对。审批沿用原会话权限。" : "Notifications stay local. Tasks cannot run during sleep or sign-out. Unknown outcomes after restart require review. Thread permissions and approvals still apply."}</p>
  </section>;
}

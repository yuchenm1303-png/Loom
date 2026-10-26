import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ArrowRight, Check, ChevronDown, Clock3, FileText, History, Info, Loader2, MessageSquare, Pause, Play, Plus, RefreshCw, SlidersHorizontal, Square, Trash2, Zap } from "./icons";
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
  const copy = (cn: string, en: string) => zh ? cn : en;
  const [data, setData] = useState<Snapshot | null>(null);
  const [draft, setDraft] = useState<Task>(() => ({ ...fresh(), threadId: threadId || "" }));
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [history, setHistory] = useState<Run[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [threads, setThreads] = useState<{ id: string; title?: string }[]>([]);
  const formRef = useRef<HTMLFormElement>(null);
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
  const update = (values: Partial<Task>) => { setDraft(previous => ({ ...previous, ...values })); setSaved(false); };
  function selectTask(task?: Task) {
    setDraft(task ? { ...task, nextAt: task.nextAt ?? Date.now() / 1000 + 60 } : { ...fresh(), threadId: threadId || "" });
    setSaved(false);
    setError("");
    formRef.current?.querySelector<HTMLInputElement>("input[name=name]")?.focus({ preventScroll: true });
    formRef.current?.scrollIntoView({ behavior: "auto", block: "nearest" });
  }
  async function mutate(method: string, params: Record<string, unknown>, select = false) {
    setBusy(true);
    setError("");
    try {
      const result = await window.loom.call<{ task?: Task }>(method, params);
      if (select && result.task) { setDraft(result.task); setSaved(true); }
      if (!select && result.task && params.id === draft.id) update({ enabled: result.task.enabled });
      if (method === "ambient/cancel" && params.id === draft.id) update({ enabled: false });
      if (method === "ambient/delete" && params.id === draft.id) selectTask();
      setData(await window.loom.call<Snapshot>("ambient/list", {}));
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function loadHistory() {
    setBusy(true);
    setError("");
    try {
      const result = await window.loom.call<{ runs: Run[]; nextCursor: number | null }>("ambient/history", { before: cursor });
      setHistory(old => [...old, ...result.runs.filter(run => !old.some(previous => previous.id === run.id))]);
      setCursor(result.nextCursor);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  const triggers = [
    { kind: "once", label: copy("定时", "Once"), detail: copy("在指定时间", "At a chosen time"), icon: Clock3 },
    { kind: "interval", label: copy("周期", "Recurring"), detail: copy("按固定间隔", "On an interval"), icon: RefreshCw },
    { kind: "file", label: copy("文件", "File change"), detail: copy("工作区文件变化", "Watch a local file"), icon: FileText },
    { kind: "event", label: copy("事件", "Event"), detail: copy("由适配器触发", "From an adapter"), icon: Zap },
  ];
  const triggerFor = (kind: string) => triggers.find(item => item.kind === kind) || triggers[0];
  const time = (epoch?: number | null) => epoch == null ? "—" : new Intl.DateTimeFormat(zh ? "zh-CN" : "en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(epoch * 1000);
  const status = (value: string) => ({ queued: copy("排队中", "Queued"), starting: copy("准备中", "Starting"), running: copy("执行中", "Running"), completed: copy("已完成", "Completed"), waiting_approval: copy("等待审批", "Approval needed"), needs_review: copy("待核对", "Needs review"), failed: copy("失败", "Failed"), cancelled: copy("已取消", "Cancelled"), cancelling: copy("正在停止", "Stopping"), interrupted: copy("已中断", "Interrupted"), limited: copy("已达上限", "Limit reached") }[value] || value);
  const runText = (run: Run) => run.result?.trim() === "AMBIENT_NO_CHANGE" ? copy("没有需要报告的变化", "No changes to report") : run.error || run.result || copy("等待执行结果", "Waiting for a result");
  const enabledCount = data?.tasks.filter(task => task.enabled).length ?? 0;
  const hasSchedule = draft.kind === "once" || draft.kind === "interval";
  const dateValue = draft.nextAt == null ? "" : new Date(draft.nextAt * 1000 - new Date(draft.nextAt * 1000).getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  const renderRun = (run: Run) => <div className="ambient-log-row" key={run.id}>
    <span className="ambient-run-dot" data-status={run.status} />
    <div><div className="ambient-log-meta"><strong>{status(run.status)}</strong><span>{triggers.find(item => item.kind === run.source)?.label || copy("事件", "Event")}</span><span>{(run.tokens ?? 0).toLocaleString()} tokens</span></div><p>{runText(run)}</p></div>
  </div>;
  return <section className="ambient-panel" aria-label={copy("常驻助理", "Ambient assistant")}>
    <header className="ambient-heading"><div className="ambient-eyebrow">{copy("轻量自动化", "AUTOMATIONS")}</div>
      <h1>{copy("常驻助理", "Ambient assistant")}</h1><p>{copy("让日常小事，按时发生。", "A little less to keep in mind.")}</p>
    </header>
    <div className="ambient-statusbar">
      <div className="ambient-host-state"><span className="ambient-status-dot" data-state={!data?.online ? "offline" : data.paused ? "paused" : "online"} />
        <strong>{data?.online ? copy("后台在线", "Host online") : connectionError ? copy("未连接", "Not connected") : data ? copy("后台离线", "Host offline") : copy("正在连接", "Connecting")}</strong>
        <span className="ambient-status-caption">{data?.paused ? copy("新执行已暂停", "New runs paused") : copy("空闲时不调用模型", "No model calls while idle")}</span></div>
      {data && <button className="ambient-button ambient-button-quiet" type="button" disabled={busy} onClick={() => void mutate("ambient/pause", { paused: !data.paused })}>
        {data.paused ? <Play size={13} /> : <Pause size={13} />}{data.paused ? copy("恢复", "Resume") : copy("暂停", "Pause")}</button>}
    </div>
    {(error || connectionError || data?.error) && <div className="ambient-error" role="alert"><Info size={15} /><span>{error || connectionError || data?.error}</span></div>}
    <div className="ambient-workspace">
      <form className="ambient-editor" ref={formRef} aria-busy={busy} onSubmit={e => { e.preventDefault(); if (!busy && data && draft.name.trim() && draft.threadId && draft.prompt.trim()) void mutate("ambient/put", draft, true); }}>
        <div className="ambient-section-heading"><h2>{draft.id ? copy("编辑任务", "Edit task") : copy("创建任务", "Create a task")}</h2><span>{draft.id ? copy("调整你的自动化", "Fine-tune your automation") : copy("设定一次，交给助理", "Set it up. Leave it to Loom.")}</span></div>
        <fieldset disabled={busy} className="ambient-fields">
          <div className="ambient-field-pair">
            <label className="ambient-field"><span>{copy("任务名称", "Task name")}</span><input name="name" required value={draft.name} placeholder={copy("例如：每周整理待办", "e.g. Weekly workspace review")} onChange={e => update({ name: e.target.value })} /></label>
            <label className="ambient-field"><span>{copy("执行会话", "Conversation")}</span><div className="ambient-select-wrap"><select required value={draft.threadId} onChange={e => update({ threadId: e.target.value })}><option value="">{copy("选择会话", "Choose a conversation")}</option>
              {threads.map(thread => <option key={thread.id} value={thread.id}>{thread.title || thread.id}</option>)}
              {draft.threadId && !threads.some(thread => thread.id === draft.threadId) && <option value={draft.threadId}>{draft.threadId}</option>}</select><ChevronDown size={13} /></div></label>
          </div>
          <label className="ambient-field"><span>{copy("让助理做什么", "What should Loom do?")}</span><textarea required rows={3} value={draft.prompt} placeholder={copy("描述需要检查或处理的事情，以及何时值得通知你。", "Describe the task, and what would be worth bringing to your attention.")} onChange={e => update({ prompt: e.target.value })} /></label>
          <div className="ambient-field"><span id="ambient-trigger-label">{copy("何时执行", "When to run")}</span>
            <div className="ambient-trigger-options" role="group" aria-labelledby="ambient-trigger-label">
              {triggers.map(({ kind, label, detail, icon: Icon }) => <button className="ambient-trigger" key={kind} type="button" aria-pressed={draft.kind === kind} onClick={() => update({ kind, nextAt: draft.nextAt ?? Date.now() / 1000 + 60 })}>
                <span className="ambient-trigger-top"><Icon size={15} /><span>{label}</span>{draft.kind === kind && <Check size={12} className="ambient-trigger-check" />}</span><small>{detail}</small></button>)}
            </div>
          </div>
          {hasSchedule && <div className={draft.kind === "interval" ? "ambient-field-pair" : "ambient-schedule"}>
            <label className="ambient-field"><span>{copy("下次执行", "Next run")}<em>{copy("本地时间", "Local time")}</em></span><input required type="datetime-local" step="1" value={dateValue} onChange={e => { const epoch = new Date(e.target.value).getTime() / 1000; if (Number.isFinite(epoch)) update({ nextAt: epoch }); }} /></label>
            {draft.kind === "interval" && <label className="ambient-field"><span>{copy("间隔秒数", "Interval in seconds")}</span><input required type="number" min="1" value={draft.seconds} onChange={e => update({ seconds: Number(e.target.value) })} /></label>}
          </div>}
          {draft.kind === "file" && <label className="ambient-field"><span>{copy("工作区文件路径", "Workspace file path")}</span><input required value={draft.path} placeholder="notes/weekly-review.md" onChange={e => update({ path: e.target.value })} /><small>{copy("文件变化时执行，指令和权限沿用所选会话。", "Runs when the file changes, using this conversation’s permissions.")}</small></label>}
          {draft.kind === "event" && <div className="ambient-inline-note"><Info size={14} /><span>{copy("等待适配器投递事件。外部消息平台尚未连接。", "Waits for adapter events. External messaging platforms are not connected.")}</span></div>}
          <details className="ambient-limits" onInvalidCapture={event => { event.currentTarget.open = true; }}><summary><SlidersHorizontal size={14} /><span>{copy("用量与限制", "Usage & limits")}</span><span className="ambient-limit-preview">{draft.maxRuns.toLocaleString()} {copy("次", "runs")}</span><ChevronDown size={13} /></summary>
            <div className="ambient-limits-body"><div className="ambient-field-pair">
              <label className="ambient-field"><span>{copy("执行次数上限", "Automatic turn limit")}</span><input required type="number" min="1" value={draft.maxRuns} onChange={e => update({ maxRuns: Number(e.target.value) })} /></label>
              <label className="ambient-field"><span>{copy("累计 token 上限", "Cumulative token limit")}</span><input required type="number" min="1" value={draft.maxTokens} onChange={e => update({ maxTokens: Number(e.target.value) })} /></label>
            </div><p>{copy("上限在每轮执行前检查，正在执行的一轮可能超出额度。", "Limits are checked between turns. An in-flight turn may exceed the remaining budget.")}</p></div>
          </details>
          <footer className="ambient-editor-footer">
            <label className="ambient-enable"><input type="checkbox" checked={draft.enabled} onChange={e => update({ enabled: e.target.checked })} /><span className="ambient-switch-track" aria-hidden="true" /><span>{copy("启用任务", "Task enabled")}</span></label>
            <div className="ambient-save-actions">{saved && <span className="ambient-saved" role="status"><Check size={12} />{copy("已保存", "Saved")}</span>}
              {draft.id && <button className="ambient-button ambient-button-quiet" type="button" onClick={() => selectTask()}>{copy("新建", "New")}</button>}
              <button className="ambient-button ambient-button-primary" disabled={!data || !draft.name.trim() || !draft.threadId || !draft.prompt.trim()} type="submit">{copy("保存任务", "Save task")}<ArrowRight size={14} /></button>
            </div>
          </footer>
        </fieldset>
      </form>
      <aside className="ambient-task-list" aria-label={copy("我的任务", "Your tasks")}>
        <div className="ambient-section-heading ambient-list-heading"><h2>{copy("我的任务", "Your tasks")}<span className="ambient-count">{data?.tasks.length ?? 0}</span></h2><button className="ambient-icon-button" type="button" disabled={busy} onClick={() => selectTask()} aria-label={copy("新建任务", "New task")}><Plus size={16} /></button></div>
        {data?.tasks.length ? <p className="ambient-list-caption">{copy(`${enabledCount} 个已启用`, `${enabledCount} enabled`)}</p> : null}
        {!data && !connectionError ? <div className="ambient-empty"><Loader2 size={20} /><p>{copy("正在读取任务…", "Loading tasks…")}</p></div> : !data?.tasks.length ? <div className="ambient-empty"><span className="ambient-empty-mark"><Clock3 size={23} strokeWidth={1.3} /></span><h3>{copy(connectionError ? "暂时无法读取任务" : "把一件小事交给 Loom", connectionError ? "Tasks are unavailable" : "One less thing to remember")}</h3><p>{copy(connectionError ? "恢复连接后，任务会显示在这里。" : "定时提醒、文件检查，或日常巡检。你的任务会出现在这里。", connectionError ? "Your tasks will appear when the connection is restored." : "A reminder, a file check, a quiet routine. Your tasks will live here.")}</p></div> : data.tasks.map(task => {
          const trigger = triggerFor(task.kind);
          const Icon = trigger.icon;
          const latest = data.runs.filter(run => run.task === task.id).at(-1);
          const selected = draft.id === task.id;
          return <article className="ambient-task-item" data-selected={selected} key={task.id}>
            <button className="ambient-task-open" type="button" disabled={busy} onClick={() => selectTask(task)} aria-label={copy(`编辑 ${task.name}`, `Edit ${task.name}`)} aria-pressed={selected}>
              <div className="ambient-task-title"><span className="ambient-task-glyph"><Icon size={15} /></span><h3>{task.name}</h3><span className="ambient-task-enabled" data-enabled={task.enabled} title={task.enabled ? copy("已启用", "Enabled") : copy("已禁用", "Disabled")} /></div>
              <span className="ambient-task-schedule">{trigger.label}{task.kind === "once" || task.kind === "interval" ? ` · ${time(task.nextAt)}` : task.kind === "file" ? ` · ${task.path}` : ` · ${copy("等待事件", "Waiting for an event")}`}</span>
            </button>
            {latest && <div className="ambient-task-outcome"><span className="ambient-run-dot" data-status={latest.status} /><span>{status(latest.status)}</span><span className="ambient-task-result" title={runText(latest)}>{runText(latest)}</span></div>}
            <div className="ambient-task-bottom"><span className="ambient-task-usage">{task.runsUsed ?? 0}/{task.maxRuns} {copy("次", "runs")}<i />{(task.tokensUsed ?? 0).toLocaleString()} tokens</span>
              <div className="ambient-task-actions"><button className="ambient-icon-button" type="button" disabled={busy} title={task.enabled ? copy("禁用", "Disable") : copy("启用", "Enable")} aria-label={task.enabled ? copy(`禁用 ${task.name}`, `Disable ${task.name}`) : copy(`启用 ${task.name}`, `Enable ${task.name}`)} onClick={() => void mutate("ambient/put", { id: task.id, enabled: !task.enabled, ...(task.kind === "once" && task.nextAt == null ? { nextAt: Date.now() / 1000 + 60 } : {}) })}>{task.enabled ? <Pause size={13} /> : <Play size={13} />}</button>
                <button className="ambient-icon-button" type="button" disabled={busy} title={copy("停止并禁用", "Stop and disable")} aria-label={copy(`停止 ${task.name}`, `Stop ${task.name}`)} onClick={() => void mutate("ambient/cancel", { id: task.id })}><Square size={12} /></button>
                <button className="ambient-icon-button ambient-delete" type="button" disabled={busy} title={copy("删除", "Delete")} aria-label={copy(`删除 ${task.name}`, `Delete ${task.name}`)} onClick={() => void mutate("ambient/delete", { id: task.id })}><Trash2 size={13} /></button></div>
            </div>
          </article>;
        })}
      </aside>
    </div>
    <div className="ambient-activity">
      <details className="ambient-disclosure"><summary><MessageSquare size={15} /><span>{copy("本地通知", "Local notifications")}</span><span className="ambient-count">{data?.notifications.length ?? 0}</span><ChevronDown size={14} /></summary>
        <div className="ambient-disclosure-body">{data?.notifications.length ? data.notifications.slice().reverse().map(notice => <div className="ambient-log-row" key={notice.id}><span className="ambient-run-dot" data-status={notice.status} /><div><strong>{status(notice.status)}</strong><p>{notice.text}</p></div></div>) : <p className="ambient-history-empty">{copy("有值得关注的变化时，通知会保存在这里。", "Meaningful updates will be kept here.")}</p>}</div>
      </details>
      <details className="ambient-disclosure"><summary><History size={15} /><span>{copy("执行记录", "Run history")}</span><ChevronDown size={14} /></summary>
        <div className="ambient-disclosure-body">{history.map(renderRun)}{!history.length && <p className="ambient-history-empty">{copy("查看每次自动执行的结果与用量。", "Review the result and usage of every automatic run.")}</p>}<button className="ambient-button ambient-button-quiet" type="button" disabled={busy || cursor === null} onClick={() => void loadHistory()}>{cursor === null ? copy("已显示全部", "All caught up") : history.length ? copy("加载更多", "Load more") : copy("查看记录", "View history")}<ArrowRight size={13} /></button></div>
      </details>
    </div>
    <div className="ambient-footnote"><Info size={13} /><p>{copy("关闭聊天窗口后继续等待。通知仅保存在本地；睡眠、注销期间不执行。", "Keeps waiting after chat windows close. Notifications stay local; tasks pause during sleep or sign-out.")} <span>{copy("沿用会话权限与审批。", "Conversation permissions and approvals still apply.")}</span></p></div>
  </section>;
}

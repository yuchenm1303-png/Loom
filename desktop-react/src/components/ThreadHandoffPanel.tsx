import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUpRight, GitFork, RefreshCw } from "./icons";
import { useI18n } from "../i18n";
import { openHandoff, type HandoffNavigation, type HandoffStatus, type ThreadHandoff } from "../state/threadHandoff";
import "./thread-handoff.css";

export function ThreadHandoffPanel({ threadId, navigation, navigationError, onPresenceChange }: { threadId?: string; navigation: HandoffNavigation; navigationError?: string; onPresenceChange?(id: string, visible: boolean): void }) {
  const { language } = useI18n();
  const en = language !== "zh-CN";
  const text = (zh: string, english: string) => en ? english : zh;
  const [snapshot, setSnapshot] = useState<{ id: string; value: HandoffStatus } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const currentThread = useRef(threadId);
  currentThread.current = threadId;
  const data = snapshot && snapshot.id === threadId ? snapshot.value : null;
  const visible = Boolean(data?.incoming || data?.outgoing.length || navigationError);
  useLayoutEffect(() => { onPresenceChange?.(threadId || "", visible); }, [threadId, visible, onPresenceChange]);
  const refresh = useCallback(async () => {
    if (!threadId || currentThread.current !== threadId) return;
    const ticket = ++epoch.current;
    try {
      const value = await window.loom.call<HandoffStatus>("thread/handoff_status", { threadId });
      if (ticket === epoch.current && currentThread.current === threadId) { setSnapshot({ id: threadId, value }); setError(""); }
    } catch (cause) {
      if (ticket === epoch.current && currentThread.current === threadId) setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [threadId]);
  useEffect(() => {
    ++epoch.current;
    setError("");
    void refresh();
    const unsubscribe = window.loom.onNotification(message => {
      const item = message.params?.handoff as ThreadHandoff | undefined;
      if ((message.method === "thread/handoff" && (item?.sourceThreadId === threadId || item?.threadId === threadId))
        || (message.method === "turn/completed" && message.params?.threadId === threadId)
        || (message.method === "thread/updated" && (message.params?.thread as { id?: string } | undefined)?.id === threadId)
        || message.method === "host/reconnected") void refresh();
    });
    const focused = () => { void refresh(); };
    window.addEventListener("focus", focused);
    return () => { ++epoch.current; unsubscribe(); window.removeEventListener("focus", focused); };
  }, [threadId, refresh]);
  const pending = Boolean(data && [data.incoming, ...data.outgoing].some(item => item && ["waiting", "starting", "running", "approval"].includes(item.state)));
  useEffect(() => {
    if (!pending) return;
    const interval = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    return () => clearInterval(interval);
  }, [pending, refresh]);
  if (!visible) return null;
  const labels: Record<ThreadHandoff["state"], string> = {
    waiting: text("等待原会话停妥", "Waiting for source to finish"), starting: text("正在启动接续", "Starting continuation"),
    running: text("新会话正在接续", "Continuation running"), approval: text("新会话等待批准", "Receiver needs approval"),
    completed: text("接续回合已结束", "Continuation turn ended"), failed: text("接续启动或执行失败", "Continuation failed"),
    interrupted: text("接续中断，待检查", "Continuation interrupted"),
  };
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  return <section className="thread-handoff-panel" aria-label={text("会话交接", "Conversation handoff")}>
    {navigationError || error ? <p role="alert">{navigationError || error}</p> : null}
    {[...(data?.incoming ? [data.incoming] : []), ...(data?.outgoing || []).slice(-3)].map(record => {
      const incoming = record.threadId === threadId;
      const target = incoming ? record.sourceThreadId : record.threadId;
      return <details key={`${threadId}:${record.id}`} className="thread-handoff-record" data-state={record.state}>
        <summary><GitFork size={13} /><span>{incoming ? text("从原会话接续", "Continued from source") : record.created === false ? text("接续请求已保存", "Continuation request saved") : text("已建立接续会话", "Continuation created")} · {record.title}</span><small role="status">{labels[record.state]}</small></summary>
        <div className="thread-handoff-body">
          <p>{record.packet?.summary || record.summary}</p>
          {record.packet ? <><strong>{text("接续任务", "Task to continue")}</strong><p>{record.packet.task}</p>
            {([['completed', text("已完成", "Completed")], ['nextSteps', text("下一步", "Next steps")], ['evidence', text("证据与相关文件", "Evidence and files")]] as const).map(([key, label]) => record.packet![key].length ? <div key={key}><strong>{label}</strong><ul>{record.packet![key].map((item, index) => <li key={index}>{item}</li>)}</ul></div> : null)}</> : null}
          {record.sourcePaused ? <small>{text("原会话已停止自动续跑。已有长目标与项目预算保留在原会话，不会重置成一份新额度。", "Source automation is paused. Existing goal and project budgets stay with the source; no fresh budget is granted.")}</small> : null}
          {record.error ? <p role="alert">{record.error}</p> : null}
          <div className="thread-handoff-actions">
            <button type="button" disabled={busy || record.created === false} onClick={() => { void run(async () => { await navigation.current(target); }); }}><ArrowUpRight size={12} />{incoming ? text("查看原会话", "View source") : text("打开接续会话", "Open continuation")}</button>
            {!incoming && window.loom.openThreadWindow ? <button type="button" disabled={busy || record.created === false} onClick={() => { void run(() => openHandoff(record, navigation, window.loom, "window")); }}>{text("独立窗口", "Separate window")}</button> : null}
            {["failed", "interrupted"].includes(record.state) ? <button type="button" disabled={busy} onClick={() => { void run(() => window.loom.call("thread/handoff_retry", { threadId: record.threadId, sourceThreadId: record.sourceThreadId, expectedTurnId: record.turnId })); }}><RefreshCw size={12} />{text("在同一会话重试接续", "Retry in the same conversation")}</button> : null}
          </div>
        </div>
      </details>;
    })}
  </section>;
}

import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { ThreadHandoffPanel } from "../../src/components/ThreadHandoffPanel";
import { useHandoffNavigation } from "../../src/state/useHandoffNavigation";
import type { HandoffStatus, ThreadHandoff } from "../../src/state/threadHandoff";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
const source = "11111111-1111-4111-8111-111111111111", child = "22222222-2222-4222-8222-222222222222";
const params = new URLSearchParams(location.search);
localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
let record: ThreadHandoff = { id: child, threadId: child, sourceThreadId: source, title: "接续账本导入修复", summary: "CSV 解析已经完成，下一步验证异常输入。",
  display: "window", state: "waiting", sourcePaused: true, createdAt: new Date().toISOString(), turnId: "attempt-0",
  packet: { task: "继续处理异常输入并完成回归。", summary: "CSV 解析已经完成，下一步验证异常输入。", completed: ["金额精度校验已经实现"], nextSteps: ["验证空文件和损坏行"], evidence: ["src/parser.py", "python -m pytest tests/test_parser.py"] } };
const listeners = new Set<(value: unknown) => void>();
const calls: unknown[] = [];
let failWindow = false;
Object.assign(window, { loom: {
  onNotification: (listener: (value: unknown) => void) => { listeners.add(listener); return () => listeners.delete(listener); },
  call: async (method: string, args: Record<string, string>): Promise<unknown> => {
    calls.push([method, args]);
    if (method === "thread/handoff_status") return { incoming: args.threadId === child ? { ...record } : null, outgoing: args.threadId === source ? [{ ...record, packet: undefined }] : [] } satisfies HandoffStatus;
    if (method === "thread/handoff_retry") { record = { ...record, turnId: "attempt-1", state: "running", error: "" }; emit(); return { handoff: record }; }
    return {};
  },
  ...(params.has("web") ? {} : { openThreadWindow: async (id: string) => { calls.push(["window", id]); if (failWindow) throw new Error("Window failed"); return true; } }),
} });
function emit() { for (const listener of listeners) listener({ method: "thread/handoff", params: { handoff: { ...record } } }); }
function Fixture() {
  const [id, setId] = useState(source);
  const [presence, setPresence] = useState(false);
  const current = async (next: string) => { calls.push(["current", next]); setId(next); };
  const navigation = { current, split: (next: string) => { calls.push(["split", next]); } };
  const error = useHandoffNavigation(id, navigation);
  const onPresence = useCallback((_id: string, visible: boolean) => setPresence(visible), []);
  Object.assign(window, { handoffFixture: { source, child, calls, emit, setId,
    update: (next: Partial<ThreadHandoff>) => { record = { ...record, ...next }; emit(); },
    failWindow: (value: boolean) => { failWindow = value; },
    clear: () => { record = { ...record, state: "completed" }; calls.length = 0; },
  } });
  return <div className="app-shell" style={{ height: "100vh", display: "block" }}><section className="workspace" data-handoff={presence || undefined} style={{ display: "grid", height: "100%", width: "100%" }}>
    <header className="thread-header" style={{ padding: "16px 22px" }}><strong>{id === source ? "原会话 · 账本导入" : id === child ? "接续会话 · 账本导入" : "其他会话"}</strong></header>
    <ThreadHandoffPanel threadId={id} navigation={navigation} navigationError={error} onPresenceChange={onPresence} />
    <div className="conversation-stage" data-mode-morph="idle"><div className="transcript-scroll" style={{ padding: 24 }}>任务在接续会话中执行。原会话保留交接来源和运行记录。</div></div>
    <div className="composer-stage" style={{ padding: 22 }}>继续对话…</div>
  </section></div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);

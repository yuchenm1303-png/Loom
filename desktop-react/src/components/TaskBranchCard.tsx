import { useState } from "react";
import { ArrowUpRight, GitBranch, X } from "./icons";
import "./task-branch-card.css";

export interface TaskBranchProposal { title: string; prompt: string; }

/** Only consume explicitly fenced Loom task proposals, never arbitrary JSON. */
export function parseTaskBranchProposal(answer: string): { text: string; proposal: TaskBranchProposal | null } {
  const fence = String.fromCharCode(96).repeat(3);
  const pattern = new RegExp("(^|\\n)" + fence + "loom-task\\s*\\n([\\s\\S]{1,14000}?)\\n" + fence + "(?=\\s|$)", "i");
  const match = pattern.exec(answer);
  if (!match) return { text: answer, proposal: null };
  try {
    const object = JSON.parse(match[2]) as Record<string, unknown>;
    const title = typeof object.title === "string" ? object.title.trim() : "";
    const prompt = typeof object.prompt === "string" ? object.prompt.trim() : "";
    if (!title || title.length > 120 || !prompt || prompt.length > 12000) return { text: answer, proposal: null };
    return { text: answer.replace(match[0], match[1] || "").trim(), proposal: { title, prompt } };
  } catch {
    return { text: answer, proposal: null };
  }
}

export function suggestBranchFromText(text: string): TaskBranchProposal {
  const cleaned = text.replace(/[\r\n]+/g, " ").replace(/[#*]/g, "").trim();
  return {
    title: (cleaned.slice(0, 72).trim() || "独立子任务"),
    prompt: ("请独立检查并完成下面这项任务，完成后给出具体修改、测试结果与未解决问题：\n\n" + text).slice(0, 12000),
  };
}

export function TaskBranchCard({
  proposal, threadId, messageId, onDoHere, disabled, onDismiss,
}: {
  proposal: TaskBranchProposal;
  threadId: string;
  messageId: string;
  onDoHere?: (prompt: string) => Promise<void> | void;
  disabled?: boolean;
  onDismiss(): void;
}) {
  const [title, setTitle] = useState(proposal.title);
  const [prompt, setPrompt] = useState(proposal.prompt);
  const [pending, setPending] = useState(false);
  const start = () => {
    if (!title.trim() || !prompt.trim() || pending || disabled) return;
    setPending(true);
    window.dispatchEvent(new CustomEvent("loom:branch-task-start", {
      detail: {
        parentThreadId: threadId, sourceMessageId: messageId,
        title: title.trim().slice(0, 120), prompt: prompt.trim().slice(0, 12000),
        onSettled: () => setPending(false),
      },
    }));
  };
  return (
    <section className="loom-task-branch-card" aria-label="New session">
      <header><span className="loom-task-branch-header-mark"><GitBranch size={15} /></span><strong>New session</strong>
        <button type="button" onClick={onDismiss} aria-label="Dismiss task" title="忽略建议"><X size={15} /></button>
      </header>
      <div className="loom-task-branch-fields">
        <input aria-label="子任务名称" value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} placeholder="子任务标题" />
        <textarea aria-label="子任务内容" rows={3} maxLength={12000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="描述独立任务…" />
      </div>
      <footer>
        <span>独立会话 · 同一项目</span>
        <button type="button" disabled={disabled || pending || !prompt.trim()} onClick={() => { onDismiss(); void onDoHere?.(prompt); }}>Do it here</button>
        <button type="button" className="loom-task-branch-start" disabled={disabled || pending || !title.trim() || !prompt.trim()} onClick={start}>
          {pending ? "Starting…" : "Start"} <ArrowUpRight size={13} />
        </button>
      </footer>
    </section>
  );
}

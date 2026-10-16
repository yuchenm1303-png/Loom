import { useEffect, useId, useState } from "react";
import { ArrowUpRight, ChevronDown, Check, FolderOpen, GitFork, LoaderCircle, X } from "./icons";
import { useI18n } from "../i18n";
import "./task-branch-card.css";

export interface TaskBranchProposal {
  title: string;
  summary: string;
  prompt: string;
  objective?: string;
  acceptanceCriteria?: string[];
  constraints?: string[];
  relevantFiles?: string[];
}
type PlannerResult = TaskBranchProposal & { actionable: boolean; reason?: string };

/** Parse only explicit task fences. Plain text never becomes a fabricated task. */
export function parseTaskBranchProposal(answer: string): { text: string; proposal: TaskBranchProposal | null } {
  const fence = String.fromCharCode(96).repeat(3);
  const pattern = new RegExp("(^|\\n)" + fence + "loom-task\\s*\\n([\\s\\S]{1,14000}?)\\n" + fence + "(?=\\s|$)", "i");
  const match = pattern.exec(answer);
  if (!match) return { text: answer, proposal: null };
  try {
    const data = JSON.parse(match[2]) as Record<string, unknown>;
    const title = typeof data.title === "string" ? data.title.trim() : "";
    const prompt = typeof data.prompt === "string" ? data.prompt.trim() : "";
    const summary = typeof data.summary === "string" ? data.summary.trim() : "";
    if (!title || title.length > 120 || !prompt || prompt.length > 12000) return { text: answer, proposal: null };
    const criteria = Array.isArray(data.acceptanceCriteria)
      ? data.acceptanceCriteria.filter((s): s is string => typeof s === "string").slice(0, 5) : [];
    return {
      text: answer.replace(match[0], match[1] || "").trim(),
      proposal: { title, prompt, summary: summary.slice(0, 220), acceptanceCriteria: criteria },
    };
  } catch {
    return { text: answer, proposal: null };
  }
}

/** A small review card. Manual actions ask the connected model for a plan first. */
export function TaskBranchCard({
  proposal: initial, sourceText, threadId, messageId, onDoHere, disabled, doHereDisabled, onDismiss,
}: {
  proposal?: TaskBranchProposal | null;
  sourceText?: string;
  threadId: string;
  messageId: string;
  onDoHere?: (prompt: string) => Promise<void> | void;
  disabled?: boolean;
  doHereDisabled?: boolean;
  onDismiss(): void;
}) {
  const { t } = useI18n();
  const id = useId();
  const [proposal, setProposal] = useState<TaskBranchProposal | null>(initial || null);
  const [loading, setLoading] = useState(!initial);
  const [pending, setPending] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState("");
  const [title, setTitle] = useState(initial?.title || "");
  const [prompt, setPrompt] = useState(initial?.prompt || "");
  const [summary, setSummary] = useState(initial?.summary || "");
  const [criteria, setCriteria] = useState<string[]>(initial?.acceptanceCriteria || []);

  useEffect(() => {
    if (initial) return;
    if (!threadId || !sourceText?.trim()) {
      setLoading(false);
      setError(t("branch.noSource"));
      return;
    }
    let alive = true;
    setLoading(true);
    setError("");
    void window.loom.call<PlannerResult>("thread/branch_plan", {
      parentThreadId: threadId,
      sourceMessageId: messageId,
      sourceText: sourceText.slice(0, 12000),
    }).then((response) => {
      if (!alive) return;
      if (!response.actionable) {
        setError(response.reason || t("branch.notActionable"));
        return;
      }
      setProposal(response);
      setTitle(response.title);
      setPrompt(response.prompt);
      setSummary(response.summary);
      setCriteria(response.acceptanceCriteria || []);
    }).catch((cause: unknown) => {
      if (alive) setError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [initial, threadId, messageId, sourceText, retry, t]);

  const start = () => {
    if (!title.trim() || !prompt.trim() || pending || loading || disabled) return;
    setPending(true);
    window.dispatchEvent(new CustomEvent("loom:branch-task-start", {
      detail: {
        parentThreadId: threadId, sourceMessageId: messageId,
        title: title.trim().slice(0, 120),
        prompt: prompt.trim().slice(0, 12000),
        summary: summary.slice(0, 220),
        acceptanceCriteria: criteria,
        onSettled: () => setPending(false),
      },
    }));
  };
  const ready = Boolean(proposal && title.trim() && prompt.trim() && !pending && !loading);
  return (
    <section className="loom-task-branch-card" aria-labelledby={id + "-heading"} aria-busy={loading || pending}>
      <header>
        <span className="loom-task-branch-header-mark"><GitFork size={17} /></span>
        <div className="loom-task-branch-heading">
          <small>{t("branch.suggestion")}</small>
          <strong id={id + "-heading"}>{loading ? t("branch.planning") : ready ? title : t("branch.create")}</strong>
        </div>
        <button type="button" disabled={pending} onClick={onDismiss} aria-label={t("branch.close")}><X size={15} /></button>
      </header>

      {loading ? (
        <div className="loom-branch-preparing"><LoaderCircle size={17} className="loom-branch-spinner" />{t("branch.planningHint")}</div>
      ) : error ? (
        <div className="loom-branch-plan-error" role="status">
          <span>{error}</span>
          {!initial && <button type="button" onClick={() => setRetry(value => value + 1)}>{t("branch.retry")}</button>}
        </div>
      ) : ready ? (
        <>
          <p className="loom-branch-summary">{summary || t("branch.description")}</p>
          {criteria.length ? <div className="loom-branch-criteria">
            {criteria.slice(0, expanded ? 5 : 2).map((criterion, i) =>
              <div key={i}><Check size={13} /><span>{criterion}</span></div>)}
          </div> : null}
          {expanded && (
            <div className="loom-branch-details">
              <label htmlFor={id + "-title"}>{t("branch.title")}</label>
              <input id={id + "-title"} maxLength={120} value={title} onChange={event => setTitle(event.target.value)} />
              <label htmlFor={id + "-summary"}>{t("branch.summary")}</label>
              <input id={id + "-summary"} maxLength={220} value={summary} onChange={event => setSummary(event.target.value)} />
              <label htmlFor={id + "-prompt"}>{t("branch.prompt")}</label>
              <textarea id={id + "-prompt"} rows={5} maxLength={12000} value={prompt} onChange={event => setPrompt(event.target.value)} />
              {proposal?.relevantFiles?.length ? (
                <p className="loom-task-branch-context"><FolderOpen size={13} />{proposal.relevantFiles.join(" · ")}</p>
              ) : null}
            </div>
          )}
        </>
      ) : null}

      <footer>
        <span><FolderOpen size={13} /> {t("branch.context")}</span>
        {ready ? <>
          <button type="button" className="loom-branch-expand" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
            {expanded ? t("branch.less") : t("branch.details")} <ChevronDown size={13} />
          </button>
          <button type="button" disabled={Boolean(doHereDisabled) || !onDoHere || pending} onClick={() => { onDismiss(); void onDoHere?.(prompt); }}>{t("branch.doHere")}</button>
          <button type="button" className="loom-task-branch-start" disabled={Boolean(disabled) || pending || !prompt.trim() || !title.trim()} onClick={start}>
            {pending ? <LoaderCircle size={14} className="loom-branch-spinner" /> : <ArrowUpRight size={14} />}
            {pending ? t("branch.starting") : t("branch.start")}
          </button>
        </> : null}
      </footer>
    </section>
  );
}

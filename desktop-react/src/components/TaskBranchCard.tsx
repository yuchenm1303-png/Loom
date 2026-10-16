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
    const criteria = data.acceptanceCriteria;
    // A suggestion is not a generic call to action. It must be an intentional,
    // concrete proposal with an outcome and at least two checks. An old-format
    // or unrequested model blob is shown as ordinary text, not an action card.
    if (data.actionable !== true || !title || title.length > 55 ||
        !prompt || prompt.length > 12000 || !summary || summary.length > 220 ||
        !Array.isArray(criteria) || criteria.length < 2 || criteria.length > 5 ||
        !criteria.every((item) => typeof item === "string" && item.trim().length > 0 && item.trim().length <= 220)) {
      return { text: answer, proposal: null };
    }
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
  const [manualEntry, setManualEntry] = useState(false);
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
    if (manualEntry) return;
    let alive = true;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const request = await window.loom.call<{ planId: string; status: string }>("thread/branch_plan", {
          parentThreadId: threadId,
          sourceMessageId: messageId,
          sourceText: sourceText.slice(0, 12000),
        });
        let result: PlannerResult | null = null;
        for (let attempt = 0; attempt < 100 && alive; attempt++) {
          const snapshot = await window.loom.call<{
            status: "pending" | "ready" | "error";
            proposal?: PlannerResult;
            error?: string;
          }>("thread/branch_plan_status", { parentThreadId: threadId, planId: request.planId });
          if (snapshot.status === "ready") {
            result = snapshot.proposal || null;
            break;
          }
          if (snapshot.status === "error") throw new Error(snapshot.error || t("branch.notActionable"));
          await new Promise<void>(resolve => window.setTimeout(resolve, 900));
        }
        if (!alive) return;
        if (!result) throw new Error(t("branch.planTimeout"));
        if (!result.actionable) {
          setError(result.reason || t("branch.notActionable"));
          return;
        }
        setProposal(result);
        setManualEntry(false);
        setTitle(result.title);
        setPrompt(result.prompt);
        setSummary(result.summary);
        setCriteria(result.acceptanceCriteria || []);
      } catch (cause: unknown) {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [initial, threadId, messageId, sourceText, retry, t, manualEntry]);

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
  const hasPlan = Boolean(proposal) || manualEntry;
  const ready = Boolean(hasPlan && title.trim() && prompt.trim() && !pending && !loading);
  return (
    <section className="loom-task-branch-card" aria-labelledby={id + "-heading"} aria-busy={loading || pending}>
      <header>
        <span className="loom-task-branch-header-mark"><GitFork size={17} /></span>
        <div className="loom-task-branch-heading">
          <small>{initial ? t("branch.suggestion") : t("branch.manualPlanning")}</small>
          <strong id={id + "-heading"}>{loading ? t("branch.planning") : hasPlan ? title : t("branch.create")}</strong>
        </div>
        <button type="button" disabled={pending} onClick={onDismiss} aria-label={t("branch.close")}><X size={15} /></button>
      </header>

      {loading && !manualEntry ? (
        <div className="loom-branch-preparing"><LoaderCircle size={17} className="loom-branch-spinner" />{t("branch.planningHint")}</div>
      ) : error ? (
        <div className="loom-branch-plan-error" role="status">
          <span>{error}</span>
          {!initial && <div className="loom-branch-error-actions">
            <button type="button" onClick={() => setRetry(value => value + 1)}>{t("branch.retry")}</button>
            <button type="button" onClick={() => { setError(""); setManualEntry(true); setExpanded(true); }}>{t("branch.manual")}</button>
          </div>}
        </div>
      ) : hasPlan ? (
        <>
          <p className="loom-branch-summary">{summary || (manualEntry ? t("branch.manualHint") : t("branch.description"))}</p>
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
        {hasPlan ? <>
          <button type="button" className="loom-branch-expand" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
            {expanded ? t("branch.less") : t("branch.details")} <ChevronDown size={13} />
          </button>
          <button type="button" disabled={Boolean(doHereDisabled) || !onDoHere || pending || !ready} onClick={() => { onDismiss(); void onDoHere?.(prompt); }}>{t("branch.doHere")}</button>
          <button type="button" className="loom-task-branch-start" disabled={Boolean(disabled) || pending || !prompt.trim() || !title.trim()} onClick={start}>
            {pending ? <LoaderCircle size={14} className="loom-branch-spinner" /> : <ArrowUpRight size={14} />}
            {pending ? t("branch.starting") : t("branch.start")}
          </button>
        </> : null}
      </footer>
    </section>
  );
}

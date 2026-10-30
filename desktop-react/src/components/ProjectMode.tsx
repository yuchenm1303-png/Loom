/* Persistent-project mode for one conversation: reads projectRun/overview, draws ProjectFlow,
 * and turns its gestures into project commands. It polls only while it is shown and the
 * window is visible: every 2 s while the project runs, every 12 s once it rests. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ProjectFlow } from "./ProjectFlow";
import { raisedBudget, readableError, toFlowData, toModelChoice, type ProjectOverview } from "./projectOverview";
import type { ModelSnapshot } from "../types/loom";

const LIVE_POLL_MS = 2000;
const REST_POLL_MS = 12000;
/** A read the Host has not answered by then counts as no connection: a frozen or sleeping Host never
 * fails a request, it just never answers, and one read at a time would wait on it forever. */
const READ_TIMEOUT_MS = 20000;
/** Episodes for a new program: generous, since tokens are the budget a person chooses. */
const START_EPISODES = 400;
/** The last overview of each conversation shown here: switching back to 建造 draws the hub at once
 *  (the composer's morph lands on it) and the first read then refreshes it. */
const lastOverview = new Map<string, ProjectOverview>();

export function ProjectMode({ threadId, workspace, model, models, onSwitchModel, onReasoning }: {
  threadId: string; workspace?: string; model?: string;
  /** The app's model snapshot and switch: a project's agents take the conversation's model. */
  models?: ModelSnapshot | null; onSwitchModel?(selection: string): Promise<unknown>; onReasoning?(kind: string, value: string): Promise<unknown>;
}) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? "zh" : "en";
  const [overview, setOverview] = useState<ProjectOverview | null>(() => lastOverview.get(threadId) ?? null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A failed refresh is the connection (a Host restart, sleep); it clears itself on the next good read.
  const [offline, setOffline] = useState(false);
  const alive = useRef(true);
  const serial = useRef(0);
  const reading = useRef(false);
  // The first read can take a while on a busy Host; after a moment the empty view says so.
  const [slow, setSlow] = useState(false);

  // One read at a time. A poll or a refocus while one is out waits for the next tick: when a read
  // took longer than the poll, every answer was overtaken by a newer request and dropped, and the
  // hub never appeared. A read after an action (`fresh`) still supersedes the one in flight.
  const load = useCallback(async (fresh = false) => {
    if (reading.current && !fresh) return;
    const ticket = ++serial.current;
    reading.current = true;
    try {
      let timer = 0;
      const result = await Promise.race([
        window.loom.call<ProjectOverview>("projectRun/overview", { threadId }),
        new Promise<never>((_, reject) => { timer = window.setTimeout(() => reject(new Error("timeout")), READ_TIMEOUT_MS); }),
      ]).finally(() => window.clearTimeout(timer));
      if (alive.current && ticket === serial.current) {
        lastOverview.set(threadId, result);
        setOverview(result);
        setNow(Date.now());
        setOffline(false);
      }
    } catch {
      if (alive.current && ticket === serial.current) setOffline(true);
    } finally {
      if (ticket === serial.current) reading.current = false;
    }
  }, [threadId]);

  useEffect(() => {
    alive.current = true;
    setOverview(lastOverview.get(threadId) ?? null);
    reading.current = false;
    void load(true);
    return () => { alive.current = false; };
  }, [load]);
  // Back from a terminal or an editor (a commit made there, say): read again at once.
  useEffect(() => {
    const again = () => { if (!document.hidden) void load(); };
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [load]);

  const resting = !overview?.run || ["integrated", "cancelled"].includes(overview.run.state) ||
    overview.run.state === "needs_review" || overview.run.control === "budget_limited";
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, resting ? REST_POLL_MS : LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, resting]);
  const waiting = !overview;
  useEffect(() => {
    setSlow(false);
    if (!waiting) return;
    const timer = window.setTimeout(() => setSlow(true), 1500);
    return () => window.clearTimeout(timer);
  }, [waiting]);

  const act = useCallback(async (command: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await command();
      await load(true);
    } catch (cause) {
      setError(readableError(cause instanceof Error ? cause.message : String(cause), lang));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [load, lang]);

  if (!overview) {
    return (
      <div className="pf-loading" aria-busy={!offline}>
        {offline ? (
          <p className="pf-loading-note" role="alert">
            {lang === "en" ? "Could not read this project from the Host." : "没能从 Host 读取这个项目。"}
            <button type="button" onClick={() => void load(true)}>{lang === "en" ? "Try again" : "重试"}</button>
          </p>
        ) : slow ? <p className="pf-loading-note">{lang === "en" ? "Reading the project…" : "正在读取项目…"}</p> : null}
      </div>
    );
  }
  const run = overview.run;
  const control = (action: "pause" | "resume" | "cancel", extra: Record<string, unknown> = {}) => act(() =>
    window.loom.call(`projectRun/${action}`, { operationId: crypto.randomUUID(), runId: run!.id, expectedRevision: run!.revision, ...extra }));
  const workspaceName = (workspace || "").split(/[\\/]/).filter(Boolean).pop() || "";
  const data = { ...toFlowData(overview, now, lang, model || "", workspaceName), models: toModelChoice(models) };
  const programId = overview.program?.id;

  return (
    <ProjectFlow lang={lang} data={data} actions={{
      busy,
      error: error || (offline ? (lang === "en" ? "Lost the Host; reconnecting…" : "与 Host 的连接中断，正在重连…") : ""),
      onPickFiles: window.loom.pickFiles ? () => window.loom.pickFiles() : undefined,
      onAttach: programId ? (paths) => act(() => window.loom.call("projectProgram/attach", { programId, attachments: paths })) : undefined,
      onModel: onSwitchModel ? (selection) => act(() => onSwitchModel(selection)) : undefined,
      // Its own errors show on the scale; the hub's busy state is for the run, not this.
      onReasoning: onReasoning ? async (kind, value) => { await onReasoning(kind, value); } : undefined,
      onCommitWorkspace: (message, count) => act(() => window.loom.call("projectRun/commitWorkspace", { threadId, message, expectedCount: count })),
      onStart: (objective, workers, tokens, attachments) => act(() => window.loom.call("projectProgram/create", {
        attachments,
        operationId: crypto.randomUUID(), threadId, objective, tokenBudget: tokens, maxEpisodes: START_EPISODES,
        maxWorkers: workers, maxMilestones: 20 })),
      onSteer: (text) => run && act(() => window.loom.call("projectRun/steer", { runId: run.id, text })),
      onPause: () => run && control("pause"),
      onResume: () => run && control("resume"),
      onCancel: () => run && control("cancel"),
      // Out of budget: add half again, the same step the panel offers; otherwise review the stopped work again and resume.
      // An agent waiting for approval: the person allows that one call or declines it.
      onAttention: (choice) => run && (data.attention?.action === "approval"
        ? act(() => window.loom.call("projectRun/approval", { runId: run.id, sessionId: data.attention!.agentId, decision: choice }))
        : data.attention?.action === "resume"
        ? control("resume")
        : data.attention?.action === "budget"
        ? control("resume", raisedBudget(run, overview.program))
        : act(() => window.loom.call("projectRun/recover", { operationId: crypto.randomUUID(), runId: run.id }))),
    }} />
  );
}

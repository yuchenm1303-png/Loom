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
/** Episodes for a new program: generous, since tokens are the budget a person chooses. */
const START_EPISODES = 400;

export function ProjectMode({ threadId, workspace, model, models, onSwitchModel, onReasoning }: {
  threadId: string; workspace?: string; model?: string;
  /** The app's model snapshot and switch: a project's agents take the conversation's model. */
  models?: ModelSnapshot | null; onSwitchModel?(selection: string): Promise<unknown>; onReasoning?(kind: string, value: string): Promise<unknown>;
}) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? "zh" : "en";
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A failed refresh is the connection (a Host restart, sleep); it clears itself on the next good read.
  const [offline, setOffline] = useState(false);
  const alive = useRef(true);
  const serial = useRef(0);

  const load = useCallback(async () => {
    const ticket = ++serial.current;
    try {
      const result = await window.loom.call<ProjectOverview>("projectRun/overview", { threadId });
      if (alive.current && ticket === serial.current) {
        setOverview(result);
        setNow(Date.now());
        setOffline(false);
      }
    } catch {
      if (alive.current) setOffline(true);
    }
  }, [threadId]);

  useEffect(() => {
    alive.current = true;
    setOverview(null);
    void load();
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

  const act = useCallback(async (command: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await command();
      await load();
    } catch (cause) {
      setError(readableError(cause instanceof Error ? cause.message : String(cause), lang));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [load, lang]);

  if (!overview) return <div className="pf-loading" aria-busy="true" />;
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
        : data.attention?.action === "budget"
        ? control("resume", raisedBudget(run, overview.program))
        : act(() => window.loom.call("projectRun/recover", { operationId: crypto.randomUUID(), runId: run.id }))),
    }} />
  );
}

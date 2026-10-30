/* Persistent-project mode for one conversation: reads projectRun/overview, draws ProjectFlow,
 * and turns its gestures into project commands. It polls only while it is shown and the
 * window is visible: every 2 s while the project runs, every 12 s once it rests. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ProjectFlow } from "./ProjectFlow";
import { toFlowData, type ProjectOverview } from "./projectOverview";

const LIVE_POLL_MS = 2000;
const REST_POLL_MS = 12000;
/** Episodes for a new program: generous, since tokens are the budget a person chooses. */
const START_EPISODES = 400;

export function ProjectMode({ threadId, workspace, model }: { threadId: string; workspace?: string; model?: string }) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? "zh" : "en";
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const serial = useRef(0);

  const load = useCallback(async () => {
    const ticket = ++serial.current;
    try {
      const result = await window.loom.call<ProjectOverview>("projectRun/overview", { threadId });
      if (alive.current && ticket === serial.current) {
        setOverview(result);
        setNow(Date.now());
      }
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [threadId]);

  useEffect(() => {
    alive.current = true;
    setOverview(null);
    void load();
    return () => { alive.current = false; };
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
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [load]);

  if (!overview) return <div className="pf-loading" aria-busy="true" />;
  const run = overview.run;
  const control = (action: "pause" | "resume" | "cancel", extra: Record<string, unknown> = {}) => act(() =>
    window.loom.call(`projectRun/${action}`, { operationId: crypto.randomUUID(), runId: run!.id, expectedRevision: run!.revision, ...extra }));
  const workspaceName = (workspace || "").split(/[\\/]/).filter(Boolean).pop() || "";
  const data = toFlowData(overview, now, lang, model || "", workspaceName);

  return (
    <ProjectFlow lang={lang} data={data} actions={{
      busy, error,
      onStart: (objective, workers, tokens) => act(() => window.loom.call("projectProgram/create", {
        operationId: crypto.randomUUID(), threadId, objective, tokenBudget: tokens, maxEpisodes: START_EPISODES,
        maxWorkers: workers, maxMilestones: 20 })),
      onSteer: (text) => run && act(() => window.loom.call("projectRun/steer", { runId: run.id, text })),
      onPause: () => run && control("pause"),
      onResume: () => run && control("resume"),
      onCancel: () => run && control("cancel"),
      // Out of budget: add half again, the same step the panel offers; otherwise resume after inspection.
      onAttention: () => run && (data.attention?.action === "budget"
        ? control("resume", { tokenBudget: Math.round(run.tokenBudget * 1.5), maxEpisodes: Math.round(run.maxEpisodes * 1.5) })
        : control("resume")),
    }} />
  );
}

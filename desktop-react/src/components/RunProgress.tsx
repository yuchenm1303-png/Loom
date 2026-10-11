import { Check, Clock3, Square } from "./icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLoomLanguage, type LoomLanguage } from "../i18n";
import type { MotionPresencePhase } from "../motion/useMotionPresence";
import { RUN_PHASE_MIN_DWELL_MS, RUN_PHASE_PRESENTATION_HOLD_MS } from "../presentationTiming";
import type { TranscriptItem } from "../types/loom";
import { buildActivityRows } from "./activityModel";
import { runtimeCopy, describeActivity } from "./runtimeCopy";
import { Crossfade } from "./Crossfade";
import "./run-progress.css";

interface RunProgressProps {
  modelActivity?: { turnId: string; active: boolean; contentGapSeconds: number } | null;
  items: TranscriptItem[];
  startedAt?: number | null;
  threadStatus?: string;
  currentTurnId?: string | null;
  totalTokens?: number;
  placement: "top" | "bottom";
  /** Presence of the strip itself; "exiting" shows how the run ended. */
  motionPhase?: MotionPresencePhase;
}

type PhaseKind = "urgent" | "work" | "gap";

interface Phase {
  label: string;
  kind: PhaseKind;
}

/**
 * The strip's label follows the runtime, but at a readable pace:
 * - work ("正在运行命令") shows promptly, even right after a gap label;
 * - gap labels ("正在分析结果") wait until the gap is real, so a quick tool
 *   chain never flashes them;
 * - one work label replaces another only after RUN_PHASE_MIN_DWELL_MS.
 * Approvals always cut through.
 */
function usePresentedPhase(phase: Phase): string {
  const [presented, setPresented] = useState<Phase>(phase);
  const shownAtRef = useRef(performance.now());

  // Keyed on the label, not the object: deltas recompute the phase every few
  // frames, and must not keep restarting a pending change.
  const { label, kind } = phase;
  useEffect(() => {
    if (label === presented.label) return;
    const show = () => {
      shownAtRef.current = performance.now();
      setPresented({ label, kind });
    };
    if (kind === "urgent" || presented.kind === "urgent") {
      show();
      return;
    }
    const age = performance.now() - shownAtRef.current;
    const delay = kind === "gap"
      ? RUN_PHASE_PRESENTATION_HOLD_MS
      : presented.kind === "gap" ? 60 : Math.max(60, RUN_PHASE_MIN_DWELL_MS - age);
    const timer = window.setTimeout(show, delay);
    return () => window.clearTimeout(timer);
  }, [kind, label, presented]);

  return presented.label;
}

function isRunningStatus(status?: string): boolean {
  return status === "running" || status === "started" || status === "waiting" || status === "waiting_approval"
    || status === "streaming" || status === "streaming_arguments";
}

function currentRunItems(items: TranscriptItem[], currentTurnId?: string | null): TranscriptItem[] {
  if (currentTurnId) {
    return items.filter((item) => item.turnId === currentTurnId);
  }

  let lastUserIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].type === "user_message") {
      lastUserIndex = index;
      break;
    }
  }

  return lastUserIndex >= 0 ? items.slice(lastUserIndex) : items;
}

function runStats(items: TranscriptItem[]) {
  return { activity: buildActivityRows(items.filter(item =>
    ["tool_call", "process", "file_edit"].includes(item.type))).length };
}

const AGENT_TOOLS = ["spawn_agent", "wait_agent", "send_agent_message", "list_agents", "close_agent"];

function phaseFor(items: TranscriptItem[], threadStatus: string | undefined, language: LoomLanguage): Phase {
  const copy = runtimeCopy(language);
  const work = (label: string): Phase => ({ label, kind: "work" });
  const gap = (label: string): Phase => ({ label, kind: "gap" });
  if (threadStatus === "waiting_approval") return { label: copy.runPhase.approval, kind: "urgent" };
  const runningActivity = [...items].reverse().find(item =>
    ["tool_call", "process", "file_edit", "approval"].includes(item.type) && isRunningStatus(item.status));
  if (runningActivity?.type === "approval") return { label: copy.runPhase.approval, kind: "urgent" };
  if (runningActivity) return work(copy.groupTitle([describeActivity(runningActivity, null, runningActivity.status || "running", copy, { fallbackToolLabel: "", screenshotCount: 0 }).category], true));

  const latestAssistant = [...items].reverse().find((item) => item.type === "assistant_message");
  if (latestAssistant && isRunningStatus(latestAssistant.status)) {
    const writing = String(latestAssistant.text ?? "").replace(/<think>[\s\S]*?(<\/think>|$)/i, "").trim();
    return work(writing ? copy.runPhase.reply : copy.runPhase.thinking);
  }

  // During a live turn there is often a short gap between one completed runtime
  // item and the next item/assistant delta. These labels describe that gap;
  // they only appear once it lasts (see usePresentedPhase).
  const latestActivity = [...items].reverse().find((item) =>
    ["tool_call", "process", "file_edit"].includes(item.type),
  );
  if (latestActivity?.type === "process") return gap(copy.runPhase.commandOutput);
  if (latestActivity?.type === "tool_call" && AGENT_TOOLS.includes(String(latestActivity.toolName || ""))) {
    return gap(copy.runPhase.agentProgress);
  }
  return latestActivity || latestAssistant ? gap(copy.runPhase.next) : work(copy.runPhase.thinking);
}

function finishedLabel(threadStatus: string | undefined, language: LoomLanguage): { label: string; stopped: boolean } {
  const copy = runtimeCopy(language);
  if (threadStatus === "failed") return { label: copy.runPhase.failed, stopped: true };
  if (threadStatus === "cancelled" || threadStatus === "interrupted") return { label: copy.runPhase.stopped, stopped: true };
  if (threadStatus === "limit_reached") return { label: copy.runPhase.limit, stopped: true };
  return { label: copy.runPhase.done, stopped: false };
}

function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remaining = safe % 60;
  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function formatTokens(tokens: number | undefined): string | null {
  if (!tokens || tokens <= 0) return null;
  if (tokens >= 1000) {
    const value = tokens >= 10000 ? (tokens / 1000).toFixed(1) : (tokens / 1000).toFixed(2);
    return `${value.replace(/\.0+$/, "").replace(/(\.\d)0$/, "$1")}k tokens`;
  }
  return `${tokens} tokens`;
}

export function RunProgress({ items, startedAt, threadStatus, currentTurnId, totalTokens, placement, modelActivity, motionPhase = "entered" }: RunProgressProps) {
  const language = useLoomLanguage();
  const copy = runtimeCopy(language);
  const finishing = motionPhase === "exiting";
  const [now, setNow] = useState(() => Date.now());
  const runItems = useMemo(() => currentRunItems(items, currentTurnId), [currentTurnId, items]);
  const rawPhase = useMemo(() => phaseFor(runItems, threadStatus, language), [language, runItems, threadStatus]);
  const presentedPhase = usePresentedPhase(rawPhase);
  const stats = useMemo(() => runStats(runItems), [runItems]);
  const tokenLabel = formatTokens(totalTokens);
  const finished = finishedLabel(threadStatus, language);
  // The runtime reports a heartbeat while a model request is in flight. Only a
  // long silence is worth mentioning, and its counter must not re-key the label.
  const silentSeconds = !finishing && modelActivity?.active && modelActivity.turnId === currentTurnId
    ? Math.floor(modelActivity.contentGapSeconds)
    : 0;
  const stalled = silentSeconds >= 6;
  const phase = finishing
    ? finished.label
    : stalled
      ? copy.runPhase.stalled
      : presentedPhase;

  useEffect(() => {
    if (finishing) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [finishing, startedAt]);

  const elapsedSeconds = startedAt ? (now - startedAt) / 1000 : 0;

  return (
    <div
      className={`run-progress-frame ${placement} ${finishing ? (finished.stopped ? "is-stopped" : "is-finished") : ""}`.trim()}
      data-motion-phase={motionPhase}
      role="status"
      aria-live="polite"
    >
      <div className="run-progress-inline">
        <div className="run-progress-primary">
          <span className="run-progress-live-dot" aria-hidden="true">
            {finishing ? (finished.stopped ? <Square size={7} strokeWidth={0} fill="currentColor" /> : <Check size={10} strokeWidth={2.6} />) : null}
          </span>
          <Crossfade identity={phase} className="run-progress-phase">{phase}</Crossfade>
          {stalled ? <span className="run-progress-phase-note">{copy.runWait(silentSeconds)}</span> : null}
        </div>

        <div className="run-progress-meta">
          <span className="run-progress-meta-item"><Clock3 size={12} strokeWidth={1.8} />{formatElapsed(elapsedSeconds)}</span>
          {stats.activity ? (
            <>
              <span className="run-progress-meta-dot" aria-hidden="true" />
              <span className="run-progress-meta-item" key={stats.activity}>{copy.runSteps(stats.activity)}</span>
            </>
          ) : null}
          {tokenLabel ? <span className="run-progress-token-group"><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle">{tokenLabel}</span></span> : null}
        </div>
      </div>
    </div>
  );
}

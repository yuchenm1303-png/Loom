import { Bot, Check, Clock3, FileDiff, Square, Terminal, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLoomLanguage, type LoomLanguage } from "../i18n";
import type { MotionPresencePhase } from "../motion/useMotionPresence";
import { RUN_PHASE_MIN_DWELL_MS, RUN_PHASE_PRESENTATION_HOLD_MS } from "../presentationTiming";
import type { TranscriptItem } from "../types/loom";
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

interface RunStats {
  activity: number;
  tools: number;
  commands: number;
  files: number;
  agents: number;
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
    const matching = items.filter((item) => item.turnId === currentTurnId);
    if (matching.length) return matching;
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

function runStats(items: TranscriptItem[]): RunStats {
  const activityItems = items.filter((item) =>
    ["tool_call", "process", "file_edit", "approval", "error"].includes(item.type),
  );
  const filePaths = new Set<string>();

  for (const item of activityItems) {
    if (item.type !== "file_edit") continue;
    for (const path of item.paths ?? []) filePaths.add(path);
  }

  return {
    activity: activityItems.length,
    tools: activityItems.filter((item) => item.type === "tool_call").length,
    commands: activityItems.filter((item) => item.type === "process").length,
    files: filePaths.size,
    agents: activityItems.filter((item) => (
      item.type === "tool_call" && item.toolName === "spawn_agent"
    )).length,
  };
}

const AGENT_TOOLS = ["spawn_agent", "wait_agent", "send_agent_message", "list_agents", "close_agent"];

function phaseFor(items: TranscriptItem[], threadStatus: string | undefined, language: LoomLanguage): Phase {
  const zh = language === "zh-CN";
  const work = (label: string): Phase => ({ label, kind: "work" });
  const gap = (label: string): Phase => ({ label, kind: "gap" });
  if (threadStatus === "waiting_approval") return { label: zh ? "等待你确认" : "Waiting for your approval", kind: "urgent" };

  const activityTypes = ["tool_call", "process", "file_edit", "approval"];
  const runningActivity = [...items].reverse().find((item) =>
    activityTypes.includes(item.type) && isRunningStatus(item.status),
  );

  if (runningActivity?.type === "approval") return { label: zh ? "等待你确认" : "Waiting for your approval", kind: "urgent" };
  if (runningActivity?.type === "process") return work(zh ? "正在运行命令" : "Running a command");
  if (runningActivity?.type === "file_edit") return work(zh ? "正在编辑文件" : "Editing files");
  if (runningActivity?.type === "tool_call") {
    const name = String(runningActivity.toolName || "").toLowerCase();
    if (name === "spawn_agent") return work(zh ? "正在派出子代理" : "Starting a sub-agent");
    if (name === "wait_agent") return work(zh ? "正在等待子代理" : "Waiting for a sub-agent");
    if (name === "send_agent_message") return work(zh ? "正在协调子代理" : "Coordinating sub-agents");
    if (name === "list_agents") return work(zh ? "正在检查子代理" : "Checking sub-agents");
    if (name === "close_agent") return work(zh ? "正在关闭子代理" : "Closing a sub-agent");
    if (/^(exec|execute|exec_command|shell|run_command|command|powershell|pwsh|bash|sh|cmd)$/.test(name)) return work(zh ? "正在运行命令" : "Running a command");
    if (/^(write_workspace_text|replace_workspace_text|write_file|edit_file|apply_patch|patch_file|replace_text)$/.test(name)) return work(zh ? "正在编辑文件" : "Editing files");
    if (name.startsWith("browser")) return work(zh ? "正在操作浏览器" : "Using the browser");
    if (name.startsWith("computer")) return work(zh ? "正在操作电脑" : "Using the computer");
    if (name === "web_search") return work(zh ? "正在联网搜索" : "Searching the web");
    if (/(^|_)(read|list|search)(_|$)/.test(name)) return work(zh ? "正在查阅工作区" : "Reading the workspace");
    return work(zh ? "正在使用工具" : "Using tools");
  }

  const latestAssistant = [...items].reverse().find((item) => item.type === "assistant_message");
  if (latestAssistant && isRunningStatus(latestAssistant.status)) {
    const writing = String(latestAssistant.text ?? "").replace(/<think>[\s\S]*?(<\/think>|$)/i, "").trim();
    return work(writing ? (zh ? "正在回复" : "Writing a reply") : (zh ? "正在思考" : "Thinking"));
  }

  // During a live turn there is often a short gap between one completed runtime
  // item and the next item/assistant delta. These labels describe that gap;
  // they only appear once it lasts (see usePresentedPhase).
  const latestActivity = [...items].reverse().find((item) =>
    ["tool_call", "process", "file_edit"].includes(item.type),
  );
  if (latestActivity?.type === "process") return gap(zh ? "正在分析命令结果" : "Reading the command output");
  if (latestActivity?.type === "tool_call" && AGENT_TOOLS.includes(String(latestActivity.toolName || ""))) {
    return gap(zh ? "正在汇总子代理进度" : "Reviewing sub-agent progress");
  }
  return gap(zh ? "正在思考下一步" : "Thinking about the next step");
}

function finishedLabel(threadStatus: string | undefined, language: LoomLanguage): { label: string; stopped: boolean } {
  const zh = language === "zh-CN";
  if (threadStatus === "failed") return { label: zh ? "未能完成" : "Didn't finish", stopped: true };
  if (threadStatus === "cancelled" || threadStatus === "interrupted") return { label: zh ? "已停止" : "Stopped", stopped: true };
  if (threadStatus === "limit_reached") return { label: zh ? "已达到用量上限" : "Usage limit reached", stopped: true };
  return { label: zh ? "已完成" : "Done", stopped: false };
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
  const zh = language === "zh-CN";
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
      ? (zh ? "模型仍在思考" : "The model is still thinking")
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
          <span key={phase} className="run-progress-phase">{phase}</span>
          {stalled ? <span className="run-progress-phase-note">{zh ? `已等待 ${silentSeconds} 秒` : `${silentSeconds}s`}</span> : null}
        </div>

        <div className="run-progress-meta">
          <span className="run-progress-meta-item"><Clock3 size={12} strokeWidth={1.8} />{formatElapsed(elapsedSeconds)}</span>
          {stats.activity ? (
            <>
              <span className="run-progress-meta-dot" aria-hidden="true" />
              <span className="run-progress-meta-item" key={stats.activity}>{zh ? `${stats.activity} 步` : `${stats.activity} ${stats.activity === 1 ? "step" : "steps"}`}</span>
            </>
          ) : null}
          {stats.agents ? (
            <>
              <span className="run-progress-meta-dot" aria-hidden="true" />
              <button
                type="button"
                className="run-progress-agent-button"
                onClick={() => window.dispatchEvent(new Event("loom:sub-agents-open"))}
                title={zh ? "打开子代理工作区" : "Open sub-agent workspace"}
              >
                <Bot size={11} />
                <span>{stats.agents}</span>
                <span>{zh ? "子代理" : "agents"}</span>
              </button>
            </>
          ) : null}
          {stats.commands ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle" title={zh ? "命令" : "Commands"}><Terminal size={11} />{stats.commands}</span></> : null}
          {stats.tools ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle" title={zh ? "工具调用" : "Tool calls"}><Wrench size={11} />{stats.tools}</span></> : null}
          {stats.files ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle" title={zh ? "改动的文件" : "Changed files"}><FileDiff size={11} />{stats.files}</span></> : null}
          {tokenLabel ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle">{tokenLabel}</span></> : null}
        </div>
      </div>
    </div>
  );
}

import { Activity, Bot, Clock3, FileDiff, Terminal, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n, type LoomLanguage } from "../i18n";
import type { TranscriptItem } from "../types/loom";
import "./run-progress.css";

interface RunProgressProps {
  items: TranscriptItem[];
  startedAt?: number | null;
  threadStatus?: string;
  currentTurnId?: string | null;
  totalTokens?: number;
  placement: "top" | "bottom";
}

interface RunStats {
  activity: number;
  tools: number;
  commands: number;
  files: number;
  agents: number;
}

const PHASE_PRESENTATION_HOLD_MS = 130;

function usePresentedPhase(phase: string, urgent: boolean): string {
  const [presented, setPresented] = useState(phase);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (phase === presented) return;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;

    if (urgent) {
      setPresented(phase);
      return;
    }

    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setPresented(phase);
    }, PHASE_PRESENTATION_HOLD_MS);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [phase, presented, urgent]);

  return presented;
}

function isRunningStatus(status?: string): boolean {
  return status === "running" || status === "started" || status === "waiting" || status === "waiting_approval";
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

function phaseFor(items: TranscriptItem[], threadStatus: string | undefined, language: LoomLanguage): string {
  const zh = language === "zh-CN";
  if (threadStatus === "waiting_approval") return zh ? "等待权限确认" : "Waiting for approval";

  const activityTypes = ["tool_call", "process", "file_edit", "approval"];
  const runningActivity = [...items].reverse().find((item) =>
    activityTypes.includes(item.type) && isRunningStatus(item.status),
  );

  if (runningActivity?.type === "approval") return zh ? "等待权限确认" : "Waiting for approval";
  if (runningActivity?.type === "process") return zh ? "正在运行命令" : "Running command";
  if (runningActivity?.type === "file_edit") return zh ? "正在编辑文件" : "Editing files";
  if (runningActivity?.type === "tool_call") {
    if (runningActivity.toolName === "spawn_agent") return zh ? "正在派出子代理" : "Spawning a sub-agent";
    if (runningActivity.toolName === "wait_agent") return zh ? "正在等待子代理" : "Waiting for sub-agent";
    if (runningActivity.toolName === "send_agent_message") return zh ? "正在协调子代理" : "Coordinating sub-agent";
    if (runningActivity.toolName === "list_agents") return zh ? "正在检查子代理" : "Checking sub-agents";
    if (runningActivity.toolName === "close_agent") return zh ? "正在关闭子代理" : "Closing sub-agent";
    return zh ? "正在使用工具" : "Using tools";
  }

  // During a live turn there is often a short gap between one completed runtime
  // item and the next item/assistant delta. Do not call that gap "preparing the
  // response" just because an earlier assistant message exists: that made long
  // command/tool sequences look frozen even while Loom was actively deciding
  // the next step.
  const latestActivity = [...items].reverse().find((item) =>
    ["tool_call", "process", "file_edit"].includes(item.type),
  );

  if (latestActivity?.type === "process") return zh ? "正在分析命令结果" : "Analyzing command result";
  if (latestActivity?.type === "tool_call") {
    if (["spawn_agent", "wait_agent", "send_agent_message", "list_agents", "close_agent"].includes(String(latestActivity.toolName || ""))) {
      return zh ? "正在汇总子代理进度" : "Reviewing sub-agent progress";
    }
    return zh ? "正在处理工具结果" : "Processing tool result";
  }
  if (latestActivity?.type === "file_edit") return zh ? "正在检查文件修改" : "Reviewing file changes";

  const latestAssistant = [...items].reverse().find((item) => item.type === "assistant_message");
  if (latestAssistant && String(latestAssistant.text ?? "").trim()) return zh ? "正在整理回复" : "Preparing response";

  return zh ? "正在思考下一步" : "Thinking about the next step";
}

function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remaining = safe % 60;
  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function formatTokens(tokens: number | undefined, language: LoomLanguage): string | null {
  if (!tokens || tokens <= 0) return null;
  const suffix = language === "zh-CN" ? "tokens" : "tokens";
  if (tokens >= 1000) {
    const value = tokens >= 10000 ? (tokens / 1000).toFixed(1) : (tokens / 1000).toFixed(2);
    return `${value.replace(/\.0$/, "")}k ${suffix}`;
  }
  return `${tokens} ${suffix}`;
}

export function RunProgress({ items, startedAt, threadStatus, currentTurnId, totalTokens, placement }: RunProgressProps) {
  const { language } = useI18n();
  const [now, setNow] = useState(() => Date.now());
  const runItems = useMemo(() => currentRunItems(items, currentTurnId), [currentTurnId, items]);
  const rawPhase = useMemo(() => phaseFor(runItems, threadStatus, language), [language, runItems, threadStatus]);
  const phase = usePresentedPhase(rawPhase, threadStatus === "waiting_approval");
  const stats = useMemo(() => runStats(runItems), [runItems]);
  const tokenLabel = formatTokens(totalTokens, language);
  const zh = language === "zh-CN";

  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const elapsedSeconds = startedAt ? (now - startedAt) / 1000 : 0;

  return (
    <div className={`run-progress-frame ${placement}`} role="status" aria-live="polite">
      <div className="run-progress-inline">
        <div className="run-progress-primary">
          <span className="run-progress-live-dot" aria-hidden="true" />
          <span className="run-progress-brand">
            <Activity size={12} strokeWidth={1.9} />
            {zh ? "Loom 正在工作" : "Loom is working"}
          </span>
          <span className="run-progress-divider" aria-hidden="true" />
          <span key={phase} className="run-progress-phase">{phase}</span>
        </div>

        <div className="run-progress-meta">
          <span className="run-progress-meta-item"><Clock3 size={12} strokeWidth={1.8} />{zh ? "用时" : "Elapsed"} {formatElapsed(elapsedSeconds)}</span>
          <span className="run-progress-meta-dot" aria-hidden="true" />
          <span className="run-progress-meta-item">{stats.activity ? (zh ? `${stats.activity} 个过程项` : `${stats.activity} steps`) : (zh ? "准备中" : "Preparing")}</span>
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
          {stats.commands ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><Terminal size={11} />{stats.commands}</span></> : null}
          {stats.tools ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><Wrench size={11} />{stats.tools}</span></> : null}
          {stats.files ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><FileDiff size={11} />{stats.files}</span></> : null}
          {tokenLabel ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle">{tokenLabel}</span></> : null}
        </div>
      </div>

    </div>
  );
}

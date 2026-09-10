import { Activity, Clock3, FileDiff, Terminal, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
  };
}

function phaseFor(items: TranscriptItem[], threadStatus?: string): string {
  if (threadStatus === "waiting_approval") return "等待权限确认";

  const runningActivity = [...items].reverse().find((item) =>
    ["tool_call", "process", "file_edit", "approval"].includes(item.type) && isRunningStatus(item.status),
  );

  if (runningActivity?.type === "approval") return "等待权限确认";
  if (runningActivity?.type === "process") return "正在运行命令";
  if (runningActivity?.type === "file_edit") return "正在编辑文件";
  if (runningActivity?.type === "tool_call") return "正在使用工具";

  const latestAssistant = [...items].reverse().find((item) => item.type === "assistant_message");
  if (latestAssistant && String(latestAssistant.text ?? "").trim()) return "正在整理回复";

  const hasFinishedActivity = items.some((item) =>
    ["tool_call", "process", "file_edit"].includes(item.type) && !isRunningStatus(item.status),
  );
  if (hasFinishedActivity) return "正在分析结果";

  return "正在思考";
}

function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remaining = safe % 60;
  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function formatTokens(tokens?: number): string | null {
  if (!tokens || tokens <= 0) return null;
  if (tokens >= 1000) {
    const value = tokens >= 10000 ? (tokens / 1000).toFixed(1) : (tokens / 1000).toFixed(2);
    return `${value.replace(/\.0$/, "")}k tokens`;
  }
  return `${tokens} tokens`;
}

export function RunProgress({ items, startedAt, threadStatus, currentTurnId, totalTokens, placement }: RunProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  const runItems = useMemo(() => currentRunItems(items, currentTurnId), [currentTurnId, items]);
  const phase = useMemo(() => phaseFor(runItems, threadStatus), [runItems, threadStatus]);
  const stats = useMemo(() => runStats(runItems), [runItems]);
  const tokenLabel = formatTokens(totalTokens);

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
            Loom 正在工作
          </span>
          <span className="run-progress-divider" aria-hidden="true" />
          <span className="run-progress-phase">{phase}</span>
        </div>

        <div className="run-progress-meta">
          <span className="run-progress-meta-item"><Clock3 size={12} strokeWidth={1.8} />用时 {formatElapsed(elapsedSeconds)}</span>
          <span className="run-progress-meta-dot" aria-hidden="true" />
          <span className="run-progress-meta-item">{stats.activity ? `${stats.activity} 个过程项` : "准备中"}</span>
          {stats.commands ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><Terminal size={11} />{stats.commands}</span></> : null}
          {stats.tools ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><Wrench size={11} />{stats.tools}</span></> : null}
          {stats.files ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle"><FileDiff size={11} />{stats.files}</span></> : null}
          {tokenLabel ? <><span className="run-progress-meta-dot" aria-hidden="true" /><span className="run-progress-meta-item subtle">{tokenLabel}</span></> : null}
        </div>
      </div>

      <div className="run-progress-track" aria-hidden="true"><span /></div>
    </div>
  );
}

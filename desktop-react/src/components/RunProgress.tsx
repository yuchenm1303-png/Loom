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

function isRunningStatus(status?: string): boolean {
  return status === "running" || status === "started";
}

function currentRunItems(items: TranscriptItem[], currentTurnId?: string | null): TranscriptItem[] {
  if (currentTurnId) {
    let first = -1;
    let last = -1;
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (String(items[index]?.turnId ?? "") === currentTurnId) {
        if (last < 0) last = index;
        first = index;
      } else if (last >= 0) {
        break;
      }
    }
    if (first >= 0) return items.slice(first, last + 1);
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

function phaseFor(items: TranscriptItem[], threadStatus?: string): string {
  if (threadStatus === "waiting_approval") return "Waiting for approval…";

  let latestAssistantHasText = false;
  let hasFinishedActivity = false;

  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (["tool_call", "process", "file_edit"].includes(item.type)) {
      if (isRunningStatus(item.status)) {
        if (item.type === "process") return "Running command…";
        if (item.type === "file_edit") return "Editing files…";
        return "Running tools…";
      }
      hasFinishedActivity = true;
      continue;
    }

    if (!latestAssistantHasText && item.type === "assistant_message" && String(item.text ?? "").trim()) {
      latestAssistantHasText = true;
    }
  }

  if (latestAssistantHasText) return "正在跟进…";
  if (hasFinishedActivity) return "Thinking about the results…";
  return "Thinking…";
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

function ThinkingOrb() {
  return (
    <span className="run-progress-thinking" aria-hidden="true">
      <span className="run-progress-thinking-orbit"><i /></span>
      <span className="run-progress-thinking-core" />
    </span>
  );
}

export function RunProgress({ items, startedAt, threadStatus, currentTurnId, totalTokens, placement }: RunProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  const runItems = useMemo(() => currentRunItems(items, currentTurnId), [currentTurnId, items]);
  const phase = useMemo(() => phaseFor(runItems, threadStatus), [runItems, threadStatus]);
  const tokenLabel = formatTokens(totalTokens);

  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const elapsedSeconds = startedAt ? (now - startedAt) / 1000 : 0;

  return (
    <div className={`run-progress-frame ${placement}`} role="status" aria-live="polite">
      <div className="run-progress-content">
        <div className="run-progress-copy">
          <ThinkingOrb />
          <span className="run-progress-time">{formatElapsed(elapsedSeconds)}</span>
          {tokenLabel ? <><span className="run-progress-separator">·</span><span>{tokenLabel}</span></> : null}
          <span className="run-progress-separator">·</span>
          <span className="run-progress-phase">{phase}</span>
        </div>
        <div className="run-progress-track" aria-hidden="true"><span /></div>
      </div>
    </div>
  );
}

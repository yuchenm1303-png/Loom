import {
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Loader2,
  MessageSquare,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import "./sub-agent-workspace.css";

const SUB_AGENT_TOOLS = new Set([
  "spawn_agent",
  "send_agent_message",
  "wait_agent",
  "list_agents",
  "close_agent",
]);

type AgentStatus = "starting" | "running" | "waiting" | "completed" | "failed" | "closed" | "idle";

interface AgentEventSummary {
  id: string;
  label: string;
  status: string;
}

interface AgentCardState {
  key: string;
  sessionId: string;
  role: string;
  task: string;
  historyMode: string;
  relationStatus: string;
  sessionStatus: string;
  finalText: string;
  error: string;
  queueDepth: number;
  executionRunning: boolean;
  fallbackStatus: string;
  events: AgentEventSummary[];
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function boolValue(value: unknown): boolean {
  return value === true;
}

function agentSnapshots(item: TranscriptItem): Record<string, unknown>[] {
  const result = objectValue(item.result);
  if (!result) return [];

  const snapshots: Record<string, unknown>[] = [];
  if (stringValue(result.session_id)) snapshots.push(result);

  for (const key of ["agents", "closed"]) {
    const values = result[key];
    if (!Array.isArray(values)) continue;
    for (const value of values) {
      const snapshot = objectValue(value);
      if (snapshot && stringValue(snapshot.session_id)) snapshots.push(snapshot);
    }
  }
  return snapshots;
}

function eventLabel(toolName: string): string {
  if (toolName === "spawn_agent") return "已派出";
  if (toolName === "send_agent_message") return "已追加任务";
  if (toolName === "wait_agent") return "已同步状态";
  if (toolName === "close_agent") return "已关闭";
  if (toolName === "list_agents") return "已刷新工作区";
  return "子代理活动";
}

function mergeSnapshot(agent: AgentCardState, snapshot: Record<string, unknown>): AgentCardState {
  return {
    ...agent,
    sessionId: stringValue(snapshot.session_id) || agent.sessionId,
    role: stringValue(snapshot.role) || agent.role,
    historyMode: stringValue(snapshot.history_mode) || agent.historyMode,
    relationStatus: stringValue(snapshot.relation_status) || agent.relationStatus,
    sessionStatus: stringValue(snapshot.session_status) || agent.sessionStatus,
    finalText: stringValue(snapshot.final_text) || agent.finalText,
    error: stringValue(snapshot.error) || agent.error,
    queueDepth: numberValue(snapshot.queue_depth),
    executionRunning: snapshot.execution_running === undefined
      ? agent.executionRunning
      : boolValue(snapshot.execution_running),
  };
}

function emptyAgent(key: string): AgentCardState {
  return {
    key,
    sessionId: "",
    role: "worker",
    task: "",
    historyMode: "",
    relationStatus: "",
    sessionStatus: "",
    finalText: "",
    error: "",
    queueDepth: 0,
    executionRunning: false,
    fallbackStatus: "",
    events: [],
  };
}

function aggregateAgents(items: TranscriptItem[]): AgentCardState[] {
  const agents = new Map<string, AgentCardState>();
  let pendingIndex = 0;

  const ensure = (key: string) => {
    const current = agents.get(key);
    if (current) return current;
    const next = emptyAgent(key);
    agents.set(key, next);
    return next;
  };

  const rekey = (oldKey: string, nextKey: string) => {
    if (!oldKey || !nextKey || oldKey === nextKey) return ensure(nextKey || oldKey);
    const old = agents.get(oldKey);
    const existing = agents.get(nextKey);
    if (!old) return ensure(nextKey);
    const merged = existing
      ? {
          ...old,
          ...existing,
          task: existing.task || old.task,
          events: [...old.events, ...existing.events],
        }
      : { ...old, key: nextKey };
    agents.delete(oldKey);
    agents.set(nextKey, merged);
    return merged;
  };

  for (const item of items) {
    const toolName = stringValue(item.toolName);
    if (!SUB_AGENT_TOOLS.has(toolName)) continue;

    const args = objectValue(item.arguments) ?? {};
    const requestedAgentId = stringValue(args.agent_id);
    const snapshots = agentSnapshots(item);

    if (toolName === "list_agents") {
      for (const snapshot of snapshots) {
        const sessionId = stringValue(snapshot.session_id);
        if (!sessionId) continue;
        const current = ensure(sessionId);
        agents.set(sessionId, mergeSnapshot(current, snapshot));
      }
      continue;
    }

    let key = requestedAgentId || stringValue(snapshots[0]?.session_id);
    if (!key) {
      key = `pending:${stringValue(item.callId) || item.id || ++pendingIndex}`;
    }

    let agent = ensure(key);
    if (toolName === "spawn_agent") {
      agent.task = stringValue(args.task) || agent.task;
      agent.role = stringValue(args.role) || agent.role;
      agent.historyMode = stringValue(args.history_mode) || agent.historyMode;
    }

    for (const snapshot of snapshots) {
      const sessionId = stringValue(snapshot.session_id);
      if (sessionId && key.startsWith("pending:")) {
        agent = rekey(key, sessionId);
        key = sessionId;
      }
      agent = mergeSnapshot(agent, snapshot);
    }

    agent.fallbackStatus = stringValue(item.status) || agent.fallbackStatus;
    agent.events.push({
      id: item.id,
      label: eventLabel(toolName),
      status: stringValue(item.status) || "completed",
    });
    agents.set(key, agent);
  }

  return [...agents.values()];
}

function mergeLiveSnapshots(
  agents: AgentCardState[],
  snapshots: Record<string, unknown>[],
): AgentCardState[] {
  if (!snapshots.length) return agents;

  const byId = new Map<string, AgentCardState>();
  const pending = agents.filter((agent) => !agent.sessionId);
  for (const agent of agents) {
    if (agent.sessionId) byId.set(agent.sessionId, agent);
  }

  let pendingIndex = 0;
  for (const snapshot of snapshots) {
    const sessionId = stringValue(snapshot.session_id);
    if (!sessionId) continue;

    let agent = byId.get(sessionId);
    if (!agent && pendingIndex < pending.length) {
      agent = { ...pending[pendingIndex], key: sessionId, sessionId };
      pendingIndex += 1;
    }
    agent ??= emptyAgent(sessionId);
    byId.set(sessionId, mergeSnapshot(agent, snapshot));
  }

  const unresolved = pending.slice(pendingIndex);
  return [...byId.values(), ...unresolved];
}

function snapshotIsLive(snapshot: Record<string, unknown>): boolean {
  const status = stringValue(snapshot.session_status).toLowerCase();
  return boolValue(snapshot.execution_running)
    || status === "running"
    || status === "waiting_approval";
}

function normalizedStatus(agent: AgentCardState): AgentStatus {
  const fallback = agent.fallbackStatus.toLowerCase();
  const session = agent.sessionStatus.toLowerCase();
  const relation = agent.relationStatus.toLowerCase();

  if (agent.error || fallback === "failed" || session === "failed") return "failed";
  if (relation === "closed") return "closed";
  if (agent.executionRunning || session === "running" || fallback === "running" || fallback === "started") return "running";
  if (session === "waiting_approval" || fallback === "waiting" || fallback === "waiting_approval") return "waiting";
  if (session === "completed") return "completed";
  if (agent.key.startsWith("pending:")) return "starting";
  return session === "idle" ? "idle" : "completed";
}

function statusCopy(status: AgentStatus): string {
  if (status === "starting") return "正在创建";
  if (status === "running") return "正在工作";
  if (status === "waiting") return "等待中";
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "closed") return "已关闭";
  return "空闲";
}

function roleCopy(role: string): string {
  const value = role.trim();
  if (!value || value === "worker") return "工作子代理";
  return value.replaceAll("_", " ");
}

function shortAgentId(agent: AgentCardState): string {
  if (!agent.sessionId) return "准备中";
  return agent.sessionId.length > 8 ? agent.sessionId.slice(-8) : agent.sessionId;
}

function historyCopy(mode: string): string {
  if (mode === "none") return "独立上下文";
  if (mode === "all") return "完整上下文";
  if (mode === "recent") return "最近上下文";
  return "独立会话";
}

function StatusIcon({ status }: { status: AgentStatus }) {
  if (status === "running" || status === "starting") return <Loader2 size={13} className="sub-agent-spinner" />;
  if (status === "completed") return <CheckCircle2 size={13} />;
  if (status === "failed") return <XCircle size={13} />;
  if (status === "waiting") return <CircleAlert size={13} />;
  return <Bot size={13} />;
}

function AgentCard({ agent }: { agent: AgentCardState }) {
  const status = normalizedStatus(agent);
  const [open, setOpen] = useState(status === "failed");
  const hasDetail = Boolean(agent.finalText || agent.error || agent.events.length > 1);
  const latestEvent = agent.events.at(-1);

  return (
    <article className={`sub-agent-card status-${status} ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="sub-agent-card-header"
        onClick={() => hasDetail && setOpen((value) => !value)}
        disabled={!hasDetail}
        aria-expanded={hasDetail ? open : undefined}
      >
        <span className="sub-agent-avatar" aria-hidden="true"><Bot size={16} /></span>
        <span className="sub-agent-heading">
          <span className="sub-agent-title-row">
            <strong>{roleCopy(agent.role)}</strong>
            <span className="sub-agent-id">#{shortAgentId(agent)}</span>
          </span>
          <span className="sub-agent-task">
            {agent.task || (latestEvent ? latestEvent.label : "子代理任务")}
          </span>
        </span>
        <span className={`sub-agent-status ${status}`}>
          <StatusIcon status={status} />
          {statusCopy(status)}
        </span>
        {hasDetail ? <ChevronRight size={14} className="sub-agent-chevron" aria-hidden="true" /> : null}
      </button>

      <div className="sub-agent-card-meta">
        <span>{historyCopy(agent.historyMode)}</span>
        {agent.queueDepth > 0 ? <span>队列 {agent.queueDepth}</span> : null}
        {latestEvent ? <span>{latestEvent.label}</span> : null}
      </div>

      {hasDetail ? (
        <div className="sub-agent-detail-grid">
          <div className="sub-agent-detail-inner">
            <div className="sub-agent-detail">
              {agent.error ? (
                <div className="sub-agent-result is-error">
                  <CircleAlert size={13} />
                  <span>{agent.error}</span>
                </div>
              ) : agent.finalText ? (
                <div className="sub-agent-result">
                  <MessageSquare size={13} />
                  <span>{agent.finalText}</span>
                </div>
              ) : null}

              {agent.events.length > 1 ? (
                <div className="sub-agent-event-strip" aria-label="Sub-agent activity">
                  {agent.events.slice(-4).map((event) => (
                    <span key={event.id} className="sub-agent-event">
                      <span className={`sub-agent-event-dot ${event.status}`} />
                      {event.label}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function isSubAgentToolItem(item: TranscriptItem): boolean {
  return item.type === "tool_call" && SUB_AGENT_TOOLS.has(stringValue(item.toolName));
}

export function SubAgentWorkspace({ items }: { items: TranscriptItem[] }) {
  const transcriptAgents = useMemo(() => aggregateAgents(items), [items]);
  const threadId = String(items.find((item) => item.threadId)?.threadId || "");
  const transcriptHasLiveAgent = useMemo(
    () => transcriptAgents.some((agent) => {
      const status = normalizedStatus(agent);
      return status === "starting" || status === "running" || status === "waiting";
    }),
    [transcriptAgents],
  );
  const [liveSnapshots, setLiveSnapshots] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    if (!threadId || !window.loom?.call) return;

    let disposed = false;
    let timer: number | null = null;
    let failures = 0;

    const schedule = (delay: number) => {
      if (disposed) return;
      timer = window.setTimeout(() => void refresh(), delay);
    };

    const refresh = async () => {
      try {
        const result = await window.loom.call<{ agents?: unknown[] }>("agent/list", {
          threadId,
          includeClosed: true,
        });
        if (disposed) return;

        const snapshots = Array.isArray(result?.agents)
          ? result.agents.map(objectValue).filter((value): value is Record<string, unknown> => Boolean(value))
          : [];
        setLiveSnapshots(snapshots);
        failures = 0;

        if (snapshots.length && !snapshots.some(snapshotIsLive)) return;
        if (snapshots.some(snapshotIsLive) || transcriptHasLiveAgent) schedule(1100);
      } catch {
        failures += 1;
        if (transcriptHasLiveAgent && failures < 4) schedule(2200);
      }
    };

    void refresh();
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [threadId, transcriptHasLiveAgent]);

  const agents = useMemo(
    () => mergeLiveSnapshots(transcriptAgents, liveSnapshots),
    [liveSnapshots, transcriptAgents],
  );
  const counts = useMemo(() => {
    let running = 0;
    let completed = 0;
    let failed = 0;
    for (const agent of agents) {
      const status = normalizedStatus(agent);
      if (status === "running" || status === "starting" || status === "waiting") running += 1;
      else if (status === "failed") failed += 1;
      else completed += 1;
    }
    return { running, completed, failed };
  }, [agents]);

  if (!agents.length) return null;

  return (
    <section className={`sub-agent-workspace ${counts.running ? "is-live" : ""}`} aria-label="子代理工作区">
      <div className="sub-agent-workspace-header">
        <span className="sub-agent-workspace-mark" aria-hidden="true">
          <Bot size={15} />
          <span className="sub-agent-workspace-pulse" />
        </span>
        <span className="sub-agent-workspace-copy">
          <strong>子代理工作区</strong>
          <span>并行任务不会占用新的对话</span>
        </span>
        <span className="sub-agent-workspace-summary">
          {counts.running ? <b>{counts.running} 运行中</b> : null}
          {counts.completed ? <span>{counts.completed} 已完成</span> : null}
          {counts.failed ? <span className="is-failed">{counts.failed} 失败</span> : null}
        </span>
      </div>

      <div className="sub-agent-grid">
        {agents.map((agent) => <AgentCard key={agent.key} agent={agent} />)}
      </div>
    </section>
  );
}

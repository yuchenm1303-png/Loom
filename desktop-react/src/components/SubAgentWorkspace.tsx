import {
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Loader2,
  MessageSquare,
  X,
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
type AgentFilter = "all" | "active" | "failed" | "done";

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

function isActiveStatus(status: AgentStatus): boolean {
  return status === "starting" || status === "running" || status === "waiting";
}

function statusRank(status: AgentStatus): number {
  if (status === "running" || status === "starting") return 0;
  if (status === "waiting") return 1;
  if (status === "failed") return 2;
  if (status === "idle") return 3;
  if (status === "completed") return 4;
  return 5;
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

function taskPreview(task: string, latestEvent?: AgentEventSummary): string {
  const source = task
    .replace(/\r?\n+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!source) return latestEvent?.label || "子代理任务";

  const taskMarker = source.match(/(?:任务|目标|负责|请完成|只做|工作内容)[：:]\s*(.+)$/i);
  let value = (taskMarker?.[1] || source)
    .replace(/^你是[^。.!！?？]{0,120}(?:子代理|执行者|验证者|测试者|修复者)[）)】\]。,.，:：\s-]*/i, "")
    .replace(/^你是[^。.!！?？]{0,100}[。.!！?？]\s*/i, "")
    .replace(/^(?:工作目录|workspace)[：:]?\s*[^。.!！?？]{1,140}[。.!！?？]\s*/i, "")
    .trim();

  if (!value) value = source;
  return value.length > 150 ? `${value.slice(0, 148).trimEnd()}…` : value;
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
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(agent.finalText || agent.error || agent.events.length > 1);
  const latestEvent = agent.events.at(-1);
  const preview = taskPreview(agent.task, latestEvent);

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
          <span className="sub-agent-task" title={preview}>
            {preview}
          </span>
        </span>
        <span className={`sub-agent-status ${status}`}>
          <StatusIcon status={status} />
          {statusCopy(status)}
        </span>
        {hasDetail ? <ChevronRight size={14} className="sub-agent-chevron" aria-hidden="true" /> : null}
      </button>

      <div className="sub-agent-card-meta">
        <span className="sub-agent-context-label">{historyCopy(agent.historyMode)}</span>
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

export function SubAgentWorkspace({
  items,
  active = false,
  docked = false,
  onClose,
}: {
  items: TranscriptItem[];
  active?: boolean;
  docked?: boolean;
  onClose?(): void;
}) {
  const transcriptAgents = useMemo(() => aggregateAgents(items), [items]);
  const threadId = String(items.find((item) => item.threadId)?.threadId || "");
  const knownAgentIds = useMemo(
    () => transcriptAgents.map((agent) => agent.sessionId).filter(Boolean),
    [transcriptAgents],
  );
  const knownAgentIdsKey = knownAgentIds.join("|");
  const transcriptHasLiveAgent = useMemo(
    () => transcriptAgents.some((agent) => {
      const status = normalizedStatus(agent);
      return status === "starting" || status === "running" || status === "waiting";
    }),
    [transcriptAgents],
  );
  const [liveSnapshots, setLiveSnapshots] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    if (!active) setLiveSnapshots([]);
  }, [active, threadId]);

  useEffect(() => {
    if (!active || !threadId || !knownAgentIds.length || !window.loom?.call) return;

    let disposed = false;
    let timer: number | null = null;
    let failures = 0;
    const allowedIds = new Set(knownAgentIds);

    function schedule(delay: number) {
      if (disposed) return;
      timer = window.setTimeout(() => void refresh(), delay);
    }

    async function refresh() {
      try {
        const result = await window.loom.call<{ agents?: unknown[] }>("agent/list", {
          threadId,
          includeClosed: true,
        });
        if (disposed) return;

        const snapshots = Array.isArray(result?.agents)
          ? result.agents
              .map(objectValue)
              .filter((value): value is Record<string, unknown> => (
                Boolean(value) && allowedIds.has(stringValue(value?.session_id))
              ))
          : [];
        setLiveSnapshots(snapshots);
        failures = 0;

        if (snapshots.length && !snapshots.some(snapshotIsLive)) return;
        if (snapshots.some(snapshotIsLive) || transcriptHasLiveAgent) schedule(1100);
      } catch {
        failures += 1;
        if (transcriptHasLiveAgent && failures < 4) schedule(2200);
      }
    }

    void refresh();
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [active, knownAgentIdsKey, threadId, transcriptHasLiveAgent]);

  const agents = useMemo(
    () => mergeLiveSnapshots(transcriptAgents, liveSnapshots),
    [liveSnapshots, transcriptAgents],
  );
  const [filter, setFilter] = useState<AgentFilter>("all");
  const counts = useMemo(() => {
    let running = 0;
    let completed = 0;
    let failed = 0;
    for (const agent of agents) {
      const status = normalizedStatus(agent);
      if (isActiveStatus(status)) running += 1;
      else if (status === "failed") failed += 1;
      else completed += 1;
    }
    return { running, completed, failed };
  }, [agents]);

  const sortedAgents = useMemo(
    () => [...agents].sort((left, right) => (
      statusRank(normalizedStatus(left)) - statusRank(normalizedStatus(right))
    )),
    [agents],
  );
  const visibleAgents = useMemo(
    () => sortedAgents.filter((agent) => {
      const status = normalizedStatus(agent);
      if (filter === "active") return isActiveStatus(status);
      if (filter === "failed") return status === "failed";
      if (filter === "done") return !isActiveStatus(status);
      return true;
    }),
    [filter, sortedAgents],
  );

  useEffect(() => {
    if (filter === "active" && counts.running === 0) setFilter("all");
    if (filter === "failed" && counts.failed === 0) setFilter("all");
  }, [counts.failed, counts.running, filter]);

  if (!agents.length && !docked) return null;

  return (
    <section className={`sub-agent-workspace ${counts.running ? "is-live" : ""} ${docked ? "is-docked" : ""}`.trim()} aria-label="子代理工作区">
      <div className="sub-agent-workspace-header">
        <span className="sub-agent-workspace-mark" aria-hidden="true">
          <Bot size={15} />
          <span className="sub-agent-workspace-pulse" />
        </span>
        <span className="sub-agent-workspace-copy">
          <strong>子代理工作区</strong>
          <span>{agents.length ? `${agents.length} 个代理 · 并行任务集中在这里管理` : "并行任务集中在这里管理"}</span>
        </span>
        <span className="sub-agent-workspace-summary">
          {counts.running ? <b>{counts.running} 运行中</b> : null}
          {counts.completed ? <span>{counts.completed} 已完成</span> : null}
          {counts.failed ? <span className="is-failed">{counts.failed} 失败</span> : null}
          {!agents.length ? <span className="is-empty">0 个代理</span> : null}
        </span>
        {docked && onClose ? (
          <button type="button" className="sub-agent-workspace-close" onClick={onClose} title="关闭子代理工作区" aria-label="关闭子代理工作区">
            <X size={16} strokeWidth={1.8} />
          </button>
        ) : null}
      </div>

      {agents.length ? (
        <div className="sub-agent-toolbar">
          <div className="sub-agent-filter" role="tablist" aria-label="筛选子代理">
            <button type="button" role="tab" aria-selected={filter === "all"} className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
              全部 <span>{agents.length}</span>
            </button>
            <button type="button" role="tab" aria-selected={filter === "active"} className={filter === "active" ? "active" : ""} onClick={() => setFilter("active")}>
              进行中 <span>{counts.running}</span>
            </button>
            <button type="button" role="tab" aria-selected={filter === "failed"} className={filter === "failed" ? "active" : ""} onClick={() => setFilter("failed")}>
              失败 <span>{counts.failed}</span>
            </button>
            <button type="button" role="tab" aria-selected={filter === "done"} className={filter === "done" ? "active" : ""} onClick={() => setFilter("done")}>
              已结束 <span>{agents.length - counts.running}</span>
            </button>
          </div>
          <span className="sub-agent-toolbar-note">
            {counts.running ? `${counts.running} 个任务正在并行执行` : "全部任务状态已同步"}
          </span>
        </div>
      ) : null}

      <div className={`sub-agent-grid ${agents.length ? "" : "is-empty"}`.trim()}>
        {agents.length ? (
          visibleAgents.length ? visibleAgents.map((agent) => <AgentCard key={agent.key} agent={agent} />) : (
            <div className="sub-agent-filter-empty">当前筛选下没有子代理</div>
          )
        ) : (
          <div className="sub-agent-empty-state">
            <div className="sub-agent-empty-visual" aria-hidden="true">
              <span className="sub-agent-empty-orbit orbit-one" />
              <span className="sub-agent-empty-orbit orbit-two" />
              <span className="sub-agent-empty-core"><Bot size={22} strokeWidth={1.65} /></span>
              <span className="sub-agent-empty-dot dot-one" />
              <span className="sub-agent-empty-dot dot-two" />
            </div>
            <strong>暂无子代理</strong>
            <p>当 Loom 把复杂任务分派给子代理时，它们会在这里出现，并持续显示运行状态与结果。</p>
            <div className="sub-agent-empty-chips" aria-hidden="true">
              <span>独立上下文</span>
              <span>并行执行</span>
              <span>结果汇总</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

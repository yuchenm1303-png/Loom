import { useState } from "react";
import { ChevronDown } from "../components/icons";
import { AgentHistory, AgentRunBox, useAgentPeek } from "../components/AgentRunBox";
import { TickText } from "../components/TickText";
import type { AgentHistoryPage, AgentPeek } from "../components/agentPeek";
import type { ProjectOverview, ConversationAgent } from "../components/projectOverview";
import { MotionRows, MotionValue } from "./MotionRows";
import { AnimatedFold } from "../motion/AnimatedFold";

function agentState(agent: ConversationAgent) {
  // Closed by the Host after it reported: its result stays, so it never reads as waiting to be collected.
  if (agent.finished) return ({ done: "已完成", failed: "没做完", stopped: "已停止" } as Record<string, string>)[agent.outcome || "done"] || "已完成";
  if (agent.state === "waiting_approval") return "等待批准";
  if (agent.executionRunning) return "运行中";
  if (agent.queueDepth > 0) return "排队中";
  return ({ completed: "已完成 · 等待收取结果", failed: "执行失败", cancelled: "已停止", limit_reached: "达到限制", running: "当前未执行", idle: "待命" } as Record<string, string>)[agent.state] || agent.state;
}
const roles: Record<string, string> = { "goal-check": "独立检查", "goal-line": "并行支路", worker: "执行代理", reviewer: "复审代理", planner: "规划代理" };
const peekCall = (threadId: string) => (agentId: string) => window.loom.call<AgentPeek>("projectAgent/peek", { threadId, agentId });

/** Every sub-agent the conversation ever had, as one card on the agents page; each opens into the same run box. */
export function AgentRecord({ threadId }: { threadId: string }) {
  const load = useState(() => (offset: number) => window.loom.call<AgentHistoryPage>("projectAgent/history", { threadId, offset }))[0];
  return <AgentHistory loadPage={load} loadPeek={peekCall(threadId)} />;
}

export function ConversationAgents({ overview, online, onOpen, threadId }: { overview: ProjectOverview; online: boolean; onOpen(id: string): void; threadId?: string }) {
  const [peekId, setPeekId] = useState<string | null>(null);
  const loadPeek = useState(() => (threadId ? peekCall(threadId) : undefined))[0];
  const { peek, error } = useAgentPeek(loadPeek, peekId);
  const projectIds = new Set(overview.agents.map(agent => agent.id));
  const agents = overview.conversationAgents?.filter(agent => !projectIds.has(agent.id)) || [];
  const unavailable = overview.conversationAgents === undefined;
  return <>
    <AnimatedFold open={Boolean(overview.conversationAgentsError)}><p role="alert" className="m-alert">子代理读取失败：{overview.conversationAgentsError}。请刷新重试。</p></AnimatedFold>
    <AnimatedFold open={!agents.length && !overview.conversationAgentsError}><p className="m-note">{unavailable
      ? "电脑后台尚未提供对话子代理列表，请更新电脑端。长目标的检查状态不代表列表已同步。"
      : overview.agents.length ? "没有额外的对话子代理。" : "当前没有未关闭的子代理。已结束的执行记录可在对话工具流中查看。"}</p></AnimatedFold>
    <MotionRows items={agents} itemKey={agent => agent.id}>{agent => <div className="m-agent-item" data-open={peekId === agent.id || undefined}><article className="m-agent-card">
      <div role={loadPeek ? "button" : undefined} tabIndex={loadPeek ? 0 : undefined} aria-expanded={loadPeek ? peekId === agent.id : undefined}
           className={loadPeek ? "m-agent-tap" : undefined}
           onClick={loadPeek ? () => setPeekId(peekId === agent.id ? null : agent.id) : undefined}
           onKeyDown={loadPeek ? event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setPeekId(peekId === agent.id ? null : agent.id); } } : undefined}>
        <h3>{roles[agent.role] || agent.role || "子代理"}</h3>
        <p className="m-agent-state" role="status"><MotionValue value={agentState(agent)}>{agentState(agent)}</MotionValue></p>
        <p>{agent.finished ? (agent.result ? (agent.result.length > 220 ? `${agent.result.slice(0, 218)}…` : agent.result) : "没有留下文字结果，打开执行对话查看") : agent.activity || (agent.executionRunning ? "正在处理任务，等待新的活动记录" : "打开执行对话查看详情")}</p>
        {agent.error ? <p className="m-alert">{agent.error}</p> : null}
        <p className="m-note">{agent.finished ? `${agent.tokens ? `${Math.round(agent.tokens / 1000)}K · ` : ""}${agent.id.slice(0, 8)}` : <>第 <TickText value={String(agent.step)} /> 步 · {agent.id.slice(0, 8)}</>}</p>
      </div>
      <span className="m-agent-chevron" aria-hidden="true"><ChevronDown size={18} strokeWidth={1.75} /></span>
    </article>
    {peekId === agent.id ? <div className="m-agent-peek">
      <AgentRunBox peek={peek} error={error} />
      <button type="button" className="m-button" disabled={!online} onClick={() => onOpen(agent.id)}>打开完整对话</button>
    </div> : null}
    </div>}</MotionRows>
  </>;
}

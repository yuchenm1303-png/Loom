import { ChevronRight } from "../components/icons";
import type { ProjectOverview, ConversationAgent } from "../components/projectOverview";
import { MotionRows, MotionValue } from "./MotionRows";
import { AnimatedFold } from "../motion/AnimatedFold";

function agentState(agent: ConversationAgent) {
  if (agent.state === "waiting_approval") return "等待批准";
  if (agent.executionRunning) return "运行中";
  if (agent.queueDepth > 0) return "排队中";
  return ({ completed: "已完成 · 等待收取结果", failed: "执行失败", cancelled: "已停止", limit_reached: "达到限制", running: "当前未执行", idle: "待命" } as Record<string, string>)[agent.state] || agent.state;
}
const roles: Record<string, string> = { "goal-check": "独立检查", "goal-line": "并行支路", worker: "执行代理", reviewer: "复审代理", planner: "规划代理" };
export function ConversationAgents({ overview, online, onOpen }: { overview: ProjectOverview; online: boolean; onOpen(id: string): void }) {
  const projectIds = new Set(overview.agents.map(agent => agent.id));
  const agents = overview.conversationAgents?.filter(agent => !projectIds.has(agent.id)) || [];
  const unavailable = overview.conversationAgents === undefined;
  return <>
    <AnimatedFold open={Boolean(overview.conversationAgentsError)}><p role="alert" className="m-alert">子代理读取失败：{overview.conversationAgentsError}。请刷新重试。</p></AnimatedFold>
    <AnimatedFold open={!agents.length && !overview.conversationAgentsError}><p className="m-note">{unavailable
      ? "电脑后台尚未提供对话子代理列表，请更新电脑端。长目标的检查状态不代表列表已同步。"
      : overview.agents.length ? "没有额外的对话子代理。" : "当前没有未关闭的子代理。已结束的执行记录可在对话工具流中查看。"}</p></AnimatedFold>
    <MotionRows items={agents} itemKey={agent => agent.id}>{agent => <article className="m-agent-card">
      <div>
        <h3>{roles[agent.role] || agent.role || "子代理"}</h3>
        <p className="m-agent-state" role="status"><MotionValue value={agentState(agent)}>{agentState(agent)}</MotionValue></p>
        <p>{agent.activity || (agent.executionRunning ? "正在处理任务，等待新的活动记录" : "打开执行对话查看详情")}</p>
        {agent.error ? <p className="m-alert">{agent.error}</p> : null}
        <p className="m-note">第 {agent.step} 步 · {agent.id.slice(0, 8)}</p>
      </div>
      <button type="button" className="m-icon-button" disabled={!online} aria-label={`打开${roles[agent.role] || agent.role || "子代理"}的执行对话`} onClick={() => onOpen(agent.id)}><ChevronRight size={20} strokeWidth={1.75} /></button>
    </article>}</MotionRows>
  </>;
}

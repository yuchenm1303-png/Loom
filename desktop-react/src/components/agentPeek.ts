/* A compact look at a conversation's sub-agents: what one is doing now, and the record of every one it ever had.
 * The Host's projectAgent/peek and projectAgent/history (app/project_runner/agent_peek.py); the hub and the phone
 * draw the same data. Pure functions only, so the words can be tested without a screen. */

export type AgentOutcome = "" | "done" | "failed" | "stopped";

export interface AgentPeekRow { kind: "say" | "tool"; text: string; at?: string; running?: boolean; ok?: boolean | null }

export interface AgentPeek {
  id: string; role: string; state: string; executionRunning: boolean; finished: boolean; outcome: AgentOutcome;
  startedAt?: string | null; endedAt?: string | null; tokens: number; error: string;
  /** The latest steps, oldest first: what the agent said and what it ran. Not the whole transcript. */
  rows: AgentPeekRow[];
  result: string; observedAt: number;
}

export interface AgentHistoryRow {
  id: string; parentId: string; role: string; task: string; state: string; outcome: AgentOutcome; finished: boolean;
  startedAt?: string | null; endedAt?: string | null; tokens: number; result: string; error: string;
}

export interface AgentHistoryPage { agents: AgentHistoryRow[]; total: number; observedAt: number }

type Lang = "zh" | "en";

const ROLES: Record<string, { zh: string; en: string }> = {
  "goal-check": { zh: "独立检查", en: "Independent check" },
  "goal-line": { zh: "并行支路", en: "Parallel line" },
  investigator: { zh: "只读调查", en: "Investigation" },
  worker: { zh: "子代理", en: "Sub-agent" },
};

export function agentRoleName(role: string, lang: Lang = "zh"): string {
  const known = ROLES[role] || ROLES.worker;
  return lang === "en" ? known.en : known.zh;
}

export function tokensText(value: number): string {
  if (!value) return "";
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : `${Math.max(1, Math.round(value / 1000))}K`;
}

/** "3 分", "1 时 20 分": how long it ran, up to now while it still runs. */
export function durationText(startedAt: string | null | undefined, endedAt: string | null | undefined, now: number, lang: Lang = "zh"): string {
  const from = startedAt ? Date.parse(startedAt) : NaN;
  if (!Number.isFinite(from)) return "";
  const to = endedAt ? Date.parse(endedAt) : now;
  const minutes = Math.max(0, Math.round(((Number.isFinite(to) ? to : now) - from) / 60000));
  if (minutes < 1) return lang === "en" ? "<1 min" : "不到 1 分";
  if (minutes < 60) return lang === "en" ? `${minutes} min` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return lang === "en" ? `${hours} h${rest ? ` ${rest} min` : ""}` : `${hours} 时${rest ? ` ${rest} 分` : ""}`;
}

/** When a record happened: the time today, "昨天 14:02" the day before, else the date. */
export function whenText(iso: string | null | undefined, now: number, lang: Lang = "zh"): string {
  const stamp = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(stamp)) return "";
  const date = new Date(stamp), today = new Date(now);
  const clock = date.toLocaleTimeString(lang === "en" ? "en-US" : "zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const midnight = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((midnight(today) - midnight(date)) / 86_400_000);
  if (days === 0) return clock;
  if (days === 1) return lang === "en" ? `Yesterday ${clock}` : `昨天 ${clock}`;
  return `${date.toLocaleDateString(lang === "en" ? "en-US" : "zh-CN", { month: "numeric", day: "numeric" })} ${clock}`;
}

export function outcomeText(outcome: AgentOutcome, lang: Lang = "zh"): string {
  const words = lang === "en" ? { "": "Working", done: "Done", failed: "Not finished", stopped: "Stopped" }
    : { "": "进行中", done: "已完成", failed: "没做完", stopped: "已停止" };
  return words[outcome] ?? words[""];
}

/** The first sentence of substance of what an agent handed back: a heading above it says nothing. */
export function resultHeadline(result: string, limit = 120): string {
  const rows = (result || "").split(/\r?\n/).map((row) => row.trim()).filter(Boolean);
  const plain = (row: string) => row.replace(/^[#>*\-\s]+/, "").trim();
  const line = plain(rows.find((row) => !row.startsWith("#")) || rows[0] || "");
  return line.length > limit ? `${line.slice(0, limit - 2)}…` : line;
}

/** One record of the history list: what it was for, how it ended, how long, what it cost, when. */
export function historyEntry(row: AgentHistoryRow, now: number, lang: Lang = "zh") {
  const title = row.task || agentRoleName(row.role, lang);
  const headline = row.outcome === "failed" ? (row.error || resultHeadline(row.result) || "") : resultHeadline(row.result);
  const meta = [agentRoleName(row.role, lang), durationText(row.startedAt, row.endedAt, now, lang), tokensText(row.tokens),
                whenText(row.endedAt || row.startedAt, now, lang)].filter(Boolean).join(" · ");
  return { id: row.id, title, headline, meta, outcome: row.outcome, finished: row.finished };
}

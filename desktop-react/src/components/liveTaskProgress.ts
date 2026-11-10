import type { TranscriptItem } from "../types/loom";

export interface TaskMilestone {
  id?: string;
  type?: "implementation" | "review";
  deps?: string[];
  acceptance?: string[];
  cycles?: number;
  max_review_cycles?: number;
  feedback?: { status: "open" | "resolved"; report?: string };
  evidence_facts?: { path?: string; reference_path?: string; sha256?: string; call_id?: string; event_id?: string }[];
  step: string;
  /** dropped: left out of the list unfinished; the Host keeps it with the reason given (blocker carries it). */
  status: "pending" | "in_progress" | "completed" | "blocked" | "dropped";
  evidence?: string;
  evidence_refs?: ({ call_id: string } | { path: string })[];
  outcome?: "passed" | "failed" | "interrupted" | "not_covered" | "not_assessed";
  blocker?: string;
}

export interface TaskPlanInfo {
  steps: TaskMilestone[];
  /** The person's message the list was written for, as the Host recorded it. */
  request: string;
  /** The list belongs to an earlier request: the person has sent a new message and this turn has no list of its own yet. */
  earlier: boolean;
}

export function latestTaskPlan(items: TranscriptItem[]): TaskMilestone[] {
  return latestTaskPlanInfo(items).steps;
}

export function latestTaskPlanInfo(items: TranscriptItem[]): TaskPlanInfo {
  // Committed plan events survive a crash between the state write and tool
  // completion. Prefer that source to a possibly missing/stale tool receipt.
  const authoritative = items.filter(item => item.type === "task_plan");
  const candidates = authoritative.length ? authoritative : items;
  // The person's latest message; a goal's own continuation prompts are not theirs.
  const asked = [...items].reverse().find(item => item.type === "user_message" && item.source !== "goal")?.turnId;
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const item = candidates[index];
    if (item.type !== "task_plan" && (!["update_plan", "manage_plan"].includes(item.toolName ?? "") || item.status !== "completed" || item.ok === false)) continue;
    const result = item.result as { plan?: unknown; lifecycle?: string; dropped?: unknown; request?: string; request_turn_id?: string } | undefined;
    if (result?.lifecycle === "archived") return { steps: [], request: "", earlier: false };
    if (!Array.isArray(result?.plan)) continue;
    // A real model, asked to sum up, showed the earlier request's list as this turn's and went on doing its work.
    const serving = result.request_turn_id || item.turnId;
    const earlier = Boolean(asked && serving && serving !== asked && item.turnId !== asked);
    return { steps: planSteps(result), request: typeof result.request === "string" ? result.request : "", earlier };
  }
  return { steps: [], request: "", earlier: false };
}

function planSteps(result: { plan?: unknown; dropped?: unknown }): TaskMilestone[] {
  if (!Array.isArray(result.plan)) return [];
  const listed = result.plan.filter((entry): entry is TaskMilestone => Boolean(entry)
    && typeof entry.step === "string"
    && ["pending", "in_progress", "completed", "blocked"].includes(entry.status));
  // Steps left out unfinished stay in view: a real list once lost its training and evaluation this way.
  const dropped = (Array.isArray(result.dropped) ? result.dropped : [])
    .filter((entry): entry is { step: string; reason?: string; shared?: boolean } => Boolean(entry) && typeof entry.step === "string")
    // A reason that came with a whole replacement is the list's, not this step's: it is not shown beside it.
    .map((entry): TaskMilestone => ({ step: entry.step, status: "dropped", blocker: entry.shared ? undefined : entry.reason || undefined }));
  return [...listed, ...dropped];
}

/** Presentation only: preserve every item and every intervention, never infer finality. */
export function liveTaskProgress(items: TranscriptItem[], protectedIds = new Set<string>()) {
  let latest = -1;
  items.forEach((item, index) => {
    if (item.type === "assistant_message" && item.phase === "commentary") latest = index;
  });
  const earlier: TranscriptItem[] = [];
  const current: TranscriptItem[] = [];
  items.forEach((item, index) => {
    const intervention = protectedIds.has(item.id)
      || ["user_message", "approval", "error", "file_edit"].includes(item.type)
      || ["started", "running", "waiting", "streaming", "failed", "denied"].includes(item.status ?? "")
      || (item.type === "assistant_message" && item.phase !== "commentary");
    (index < latest && !intervention ? earlier : current).push(item);
  });
  return { earlier, current };
}


/** Durable references are visible receipts, never proof inferred from prose. */
export function milestoneEvidenceLabels(step: TaskMilestone, language: "en" | "zh-CN" = "zh-CN"): string[] {
  const english = language === "en";
  return (step.evidence_refs ?? []).flatMap(ref => {
    if ("call_id" in ref && typeof ref.call_id === "string") {
      return [`${english ? "Tool record" : "工具记录"}：${ref.call_id}`];
    }
    if ("path" in ref && typeof ref.path === "string") {
      const fact = step.evidence_facts?.find(fact => fact.reference_path === ref.path || fact.path === ref.path || fact.path?.replaceAll("\\", "/") === ref.path.replaceAll("\\", "/"));
      return [`${english ? "File" : "文件"}：${ref.path}${fact?.sha256 ? ` · SHA-256 ${fact.sha256}` : ""}`];
    }
    return [];
  });
}

export function milestoneOutcomeLabel(step: TaskMilestone, language: "en" | "zh-CN" = "zh-CN"): string | undefined {
  const english = language === "en";
  if (!step.outcome) return step.status === "completed" ? (english ? "No acceptance recorded" : "未记录验收结论") : undefined;
  return english
    ? { passed: "Accepted", failed: "Acceptance failed", interrupted: "Test interrupted", not_covered: "Not covered", not_assessed: "Not assessed" }[step.outcome]
    : { passed: "验收通过", failed: "验收失败", interrupted: "测试中断", not_covered: "未覆盖", not_assessed: "未验收" }[step.outcome];
}

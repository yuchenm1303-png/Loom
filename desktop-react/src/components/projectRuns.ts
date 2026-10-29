export interface ProjectRun {
  id: string; revision: number; objective: string; repository: string; sourceThreadId: string;
  state: string; control: string; phase: string; reason?: string; completionPolicy?: string;
  workflowControl?: string; workflowStatus?: string; workflowError?: string; workflowOwnerId?: string;
  integrationRunId?: string; integrationTarget?: { parentRunId: string };
  tokensUsed: number; tokenBudget: number; episodesUsed: number; maxEpisodes: number;
  budgetPosition?: { knownTokens: number; inFlightTokens: number; assumedUnknownTokens: number; unknownRequests: number;
    chargedTokens: number; tokenBudget: number; hardBillingCap: boolean };
  knowledge?: { notes: string[]; acceptedChecks: number; recoveries: Record<string, number> };
  plan: { id: string; instructions: string; acceptance: string[]; state: string; dependsOn?: string[]; fileScopes?: string[] }[];
  activeEpisode?: { sessionId: string; state: string } | null;
  verificationStatus?: string; verificationFreshness?: string;
  maxWorkers?: number; teamOwnerId?: string; parallelStatus?: string;
  teamChildren?: { runId: string; taskId: string; state: string }[];
  deliveryReceipt?: { commit: string; ref: string; integration?: string; publication?: string; status: string };
}
export interface ProjectAssessment { id: string; verdict: string; summary: string; scope: string;
  checks: { criterionId: string; eventId: string }[]; issues: { taskId: string; description: string }[] }
export type ProjectAction = "start" | "pause" | "resume" | "cancel" | "deliver" | "review" | "prepareIntegration" | "applyIntegration";
export function actions(run: ProjectRun, child?: ProjectRun): ProjectAction[] {
  if (["cancelled", "integrated"].includes(run.state)) return [];
  if (run.teamOwnerId || run.workflowOwnerId) return run.state === "needs_review" && !run.activeEpisode && run.plan.length ? ["review"] : [];
  if (run.completionPolicy === "local_integrate") {
    const target = child || run;
    const result: ProjectAction[] = ["cancel"];
    if (run.state === "created") return ["start", "cancel"];
    if (run.workflowControl === "active" && run.workflowStatus !== "blocked") result.unshift("pause");
    else if (target.state !== "needs_review") result.unshift("resume");
    if (target === run && run.state === "needs_review" && !run.activeEpisode && run.plan.length) result.unshift("review");
    return result;
  }
  const result: ProjectAction[] = [];
  if (run.state === "created") result.push("start");
  if (run.state === "running" && run.control === "active") result.push("pause");
  if (run.state === "running" && run.control === "paused") result.push("resume");
  if (!["ready_for_review", "delivered"].includes(run.state)) result.push("cancel");
  if (run.state === "ready_for_review" && run.verificationStatus === "passed") result.push("deliver");
  if (run.state === "delivered") result.push(run.integrationTarget ? "applyIntegration" : "prepareIntegration");
  if (!run.activeEpisode && run.plan.length && (["ready_for_review", "needs_review"].includes(run.state) ||
      (run.state === "running" && run.control === "budget_limited" && run.phase === "verifying"))) result.push("review");
  return result;
}
export function effectiveRun(run: ProjectRun, runs: ProjectRun[]): ProjectRun {
  return runs.find(item => item.id === run.integrationRunId) || run;
}
export function budgetValues(tokens: string, episodes: string) {
  const tokenBudget = Number(tokens), maxEpisodes = Number(episodes);
  if (!tokens.trim() || !episodes.trim() || !Number.isInteger(tokenBudget) || tokenBudget < 1 || tokenBudget > 1e9 ||
      !Number.isInteger(maxEpisodes) || maxEpisodes < 1 || maxEpisodes > 10000) throw new Error("Invalid total budgets");
  return { tokenBudget, maxEpisodes };
}
export type ProjectCall = (method: string, params: Record<string, unknown>) => Promise<{ run: ProjectRun }>;
export class ProjectCommand {
  pending: { method: string; params: Record<string, unknown> } | null = null;
  private active = false;
  constructor(private call: ProjectCall, private id: () => string = () => crypto.randomUUID()) {}
  async send(method?: string, params?: Record<string, unknown>) {
    if (this.active) throw new Error("A project command is already running");
    if (method) {
      if (this.pending) throw new Error("Resolve the previous command first");
      this.pending = { method, params: { ...params, operationId: this.id() } };
    }
    if (!this.pending) throw new Error("No project command to retry");
    this.active = true;
    try {
      const result = await this.call(this.pending.method, this.pending.params);
      this.pending = null;
      return result.run;
    } finally { this.active = false; }
  }
  discard() { if (!this.active) this.pending = null; }
}

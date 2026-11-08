import type { LongGoal, OverviewMetrics } from "./projectOverview";

export const BUILD_BRANCHES = ["blockers", "evidence", "reviews", "changes", "spending", "delivery"] as const;
export type BuildBranch = typeof BUILD_BRANCHES[number];
export interface BuildEvidence {
  eventId: string; sessionId: string; candidateId: string; verdict: string; command: string;
  observed: string; subjectPaths: string[]; exitCode?: number | null; turnId?: string; source?: string; recordedAt?: string; log?: string;
}
export interface BuildInspection {
  observedAt: number; source: "project" | "goal" | "conversation"; runId?: string; revision?: number;
  goal?: LongGoal | null; freshness?: string;
  waits: { id: string; runId: string; code: string; since?: number; recordedAt?: number; retryAt?: number; detail?: string }[];
  criteria: { id: string; taskId: string; expectation: string; status: string; evidence?: BuildEvidence }[];
  reviews: { id: string; runId: string; verdict: string; scope: string; summary: string; candidateId: string;
    checks: { criterionId: string; eventId: string }[]; issues: { taskId: string; description: string }[] }[];
  tasks: { id: string; title: string; state: string; dependsOn: string[]; fileScopes: string[]; runId: string }[];
  changes: { runId: string; baseCommit: string; files: { status: string; path: string }[]; total: number; error: string }[];
  metrics?: OverviewMetrics | null;
  budget?: { used: number; limit: number; earlierMilestones: number; position?: { knownTokens: number; inFlightTokens: number;
    assumedUnknownTokens: number; unknownRequests: number; chargedTokens: number; hardBillingCap: boolean } };
  deliveries: { runId: string; state: string; branch: string; verification: string; integration: string; reason: string;
    receipt?: { commit: string; ref: string; status: string; integration?: string; publication?: string } | null }[];
  recoveries?: Record<string, number>;
}

export const buildLabels = {
  zh: { blockers: "等待与阻塞", evidence: "验收与证据", reviews: "复审与返工", changes: "改动与集成", spending: "预算与消耗", delivery: "交付与运行" },
  en: { blockers: "Waiting & blockers", evidence: "Checks & evidence", reviews: "Review & rework", changes: "Changes & integration", spending: "Budget & usage", delivery: "Delivery & runtime" },
};

export function buildStatus(value: string, en: boolean): string {
  const labels: Record<string, [string, string]> = {
    passed: ["通过", "Passed"], recorded_pass: ["历史通过", "Historical pass"], failed: ["失败", "Failed"],
    executed_pass: ["执行通过，待验收", "Execution passed; awaiting acceptance"],
    not_run: ["未记录执行", "No execution recorded"], not_assessed: ["尚未验收", "Not assessed"], unavailable: ["证据不可用", "Evidence unavailable"],
    stale: ["证据已过期", "Stale evidence"], current: ["匹配当前候选", "Matches current candidate"],
    pending: ["待处理", "Pending"], running: ["进行中", "Running"], working: ["开发中", "Implementing"],
    waiting: ["等待中", "Waiting"], reporting: ["汇报中", "Reporting"], done: ["已结束", "Finished"],
    needs_changes: ["需要返工", "Needs changes"], blocked: ["已阻塞", "Blocked"], continue: ["继续复审", "Review continuing"],
    task: ["单项复审", "Task review"], project: ["整体验收", "Project review"],
    merged: ["已合并", "Merged"], integrated: ["已本地集成", "Integrated locally"], verified: ["已验收", "Verified"],
    candidate: ["候选已提交", "Candidate submitted"], delivered: ["候选已交付", "Candidate delivered"],
    prepared: ["已准备", "Prepared"], ready_for_review: ["待交付", "Ready for delivery"],
    needs_review: ["待检查", "Needs inspection"], created: ["已创建", "Created"], cancelled: ["已停止", "Stopped"],
    active: ["进行中", "Active"], paused: ["已暂停", "Paused"], complete: ["已结束，未验收", "Ended without acceptance"], budget_limited: ["预算已用完", "Budget exhausted"],
    review_evidence_rejected: ["验收证据退回", "Evidence returned"], transient_failure_retry: ["模型服务重试", "Provider retries"],
    interrupted_episode_continued: ["中断后继续", "Continued after interruption"], episode_limit_reached: ["达到单轮上限后继续", "Continued after episode bound"],
    no_progress_retry: ["无进展后重试", "No-progress retries"], reply_rejected: ["回复格式退回", "Reply format returned"],
    scope_rejected: ["范围检查退回", "Scope check returned"], credential_retry: ["凭证失败重试", "Credential retries"],
    unknown: ["未记录", "Not recorded"], planning: ["规划", "Planning"], verifying: ["复审", "Review"],
    taskReview: ["单项复审", "Task review"], projectReview: ["整体复审", "Project review"],
    shardReview: ["分片复审", "Parallel review"], aggregateReview: ["汇总复审", "Aggregate review"],
  };
  return labels[value]?.[en ? 1 : 0] || value;
}

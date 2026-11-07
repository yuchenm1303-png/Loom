import type { ProjectRecord, ThreadRecord } from "../types/loom";
import type { ProjectOverview } from "../components/projectOverview";
import { budgetValues } from "../components/projectRuns";

const normalize = (value: string) =>
  value.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
export function inProject(
  thread: ThreadRecord,
  project: ProjectRecord,
): boolean {
  return thread.projectId
    ? thread.projectId === project.id
    : normalize(thread.workspace || "") === normalize(project.root);
}
export function searchThreads(
  threads: ThreadRecord[],
  search: string,
  project?: ProjectRecord,
) {
  const query = search.trim().toLocaleLowerCase();
  return threads.filter(
    (thread) =>
      (!project || inProject(thread, project)) &&
      (!query ||
        `${thread.title}\n${thread.workspace}`
          .toLocaleLowerCase()
          .includes(query)),
  );
}
export type BuildSettings = {
  tokens: string;
  episodes: string;
  workers: string;
  milestones: string;
  fullAccess: boolean;
};
function mobileBudgetValues(tokens: string, episodes: string) {
  try {
    return budgetValues(tokens, episodes);
  } catch {
    throw new Error(
      "token 预算须为 1–1000000000 的整数，执行轮次须为 1–10000 的整数",
    );
  }
}
export function buildStartParams(
  threadId: string,
  overview: ProjectOverview,
  settings: BuildSettings,
  attachments: string[],
) {
  if (!overview.proposal) throw new Error("方案已更新，请刷新后重试");
  if (overview.workspace?.status && overview.workspace.status !== "ready")
    throw new Error("请先准备好项目仓库");
  if (overview.workspace?.changeCount)
    throw new Error("请先检查并提交工作区改动，再启动建造");
  const maxWorkers = Number(settings.workers),
    maxMilestones = Number(settings.milestones);
  if (!Number.isInteger(maxWorkers) || maxWorkers < 1 || maxWorkers > 8)
    throw new Error("并发 Agent 数必须为 1–8");
  if (
    !Number.isInteger(maxMilestones) ||
    maxMilestones < 1 ||
    maxMilestones > 50
  )
    throw new Error("阶段上限必须为 1–50");
  return {
    threadId,
    proposalId: overview.proposal.id,
    operationId: `weave:${overview.proposal.id}`,
    ...mobileBudgetValues(settings.tokens, settings.episodes),
    maxWorkers,
    maxMilestones,
    attachments,
    permissionMode: settings.fullAccess ? "full-access" : "workspace",
  };
}
export function resumeBudgetParams(
  overview: ProjectOverview,
  tokens: string,
  episodes: string,
) {
  if (!overview.run) throw new Error("建造任务尚未就绪");
  const target = overview.integration || overview.run;
  const values = mobileBudgetValues(tokens, episodes);
  if (
    values.tokenBudget <= target.tokensUsed ||
    values.maxEpisodes <= target.episodesUsed
  )
    throw new Error("总上限必须大于当前累计用量");
  return Object.fromEntries(
    Object.entries(values).filter(
      ([key, value]) => value !== target[key as keyof typeof values],
    ),
  );
}

import type { CurrentModel } from "../types/loom";
import type { ProjectOverview } from "../components/projectOverview";
import {
  useModelUsage,
  usageScopeText,
  usageWindowLabel,
  resetText,
} from "../components/ModelUsageIndicator";
import type { BuildBranch } from "./BuildBranches";

function Quota({ selection, online }: { selection: string; online: boolean }) {
  const usage = useModelUsage(selection);
  return (
    <section className="m-card">
      <h2>供应商额度</h2>
      <button
        type="button"
        className="m-inline-button"
        disabled={!online || usage.loading}
        onClick={() => void usage.refresh(true)}
      >
        {usage.loading ? "正在读取…" : "刷新额度"}
      </button>
      {usage.data?.windows?.map((row) => (
        <div className="m-quota-window" key={row.label}>
          <strong>
            {usageWindowLabel(row.label, true)} · 剩余 {row.remainingPercent}%
          </strong>
          <meter min="0" max="100" value={row.remainingPercent} />
          <p>
            {row.resetsAt
              ? resetText(row.resetsAt, true)
              : "供应商未提供重置时间"}
          </p>
        </div>
      ))}
      {usage.data?.balances?.map((row) => (
        <p key={row.currency}>
          {row.currency} · {row.total}
        </p>
      ))}
      <p role="status">
        {usage.data?.message ||
          (usage.loading
            ? "正在向供应商查询"
            : !usage.data?.windows?.length && !usage.data?.balances?.length
              ? "供应商没有返回可展示的额度数据"
              : "以供应商报告为准")}
      </p>
      {usage.data?.stale ? (
        <p className="m-alert">当前显示上次成功查询的数据</p>
      ) : null}
      <p className="m-note">{usageScopeText(usage.data, true)}</p>
    </section>
  );
}
export function BuildBranchDetails({
  branch,
  overview,
  model,
  onModelSettings,
  online,
}: {
  branch: BuildBranch | null;
  overview: ProjectOverview | null;
  model?: CurrentModel | null;
  onModelSettings(): void;
  online: boolean;
}) {
  if (branch === "model")
    return (
      <section className="m-card">
        <h2>对话模型</h2>
        <p>{model?.name || model?.model || "模型信息尚未就绪"}</p>
        <p>{model?.groupName || model?.adapter}</p>
        <p>推理：{model?.reasoning?.value || "模型默认"}</p>
        <button
          type="button"
          className="m-button"
          disabled={!online}
          onClick={onModelSettings}
        >
          调整模型、推理与对话权限
        </button>
        <p className="m-note">
          建造运行权限与并发数在启动时固定，调整新消息的设置不会改写已启动的建造。
        </p>
      </section>
    );
  if (branch === "usage")
    return model?.selection ? (
      <Quota
        key={model.selection}
        selection={model.selection}
        online={online}
      />
    ) : (
      <p className="m-note">模型信息就绪后可查询额度。</p>
    );
  const run = overview?.run;
  if (branch === "scope") {
    const scopes = [
      ...new Set(run?.plan.flatMap((task) => task.fileScopes || []) || []),
    ];
    return (
      <section className="m-card">
        <h2>工作范围</h2>
        {scopes.length ? (
          <ul className="m-build-list">
            {scopes.map((path) => (
              <li className="m-path" key={path}>
                {path}
              </li>
            ))}
          </ul>
        ) : (
          <p>当前任务计划尚未声明文件范围。</p>
        )}
        <p className="m-note">
          范围来自 Host 的任务计划，不表示这些文件已经修改。
        </p>
      </section>
    );
  }
  if (branch === "knowledge")
    return (
      <section className="m-card">
        <h2>项目知识</h2>
        <p>已验收检查：{run?.knowledge?.acceptedChecks ?? 0}</p>
        {run?.knowledge?.notes?.length ? (
          <ol className="m-build-list">
            {run.knowledge.notes.map((note, index) => (
              <li className="m-prewrap" key={index}>
                {note}
              </li>
            ))}
          </ol>
        ) : (
          <p>尚未记录项目笔记。</p>
        )}
        {Object.entries(run?.knowledge?.recoveries || {}).map(
          ([kind, count]) => (
            <p key={kind}>
              {kind} · 自动恢复 {count} 次
            </p>
          ),
        )}
      </section>
    );
  if (branch === "history")
    return (
      <section className="m-card">
        <h2>阶段历史与运行事件</h2>
        {!overview?.program?.milestones.length && !overview?.events.length ? (
          <p>尚无建造阶段或运行事件。</p>
        ) : null}
        {overview?.program?.milestones.map((stage) => (
          <details key={stage.index}>
            <summary>
              阶段 {stage.index} · {stage.complete ? "完成" : "未完成"}
            </summary>
            <p>
              {stage.tokens.toLocaleString()} token · {stage.episodes} 轮
            </p>
            <p>{stage.summary}</p>
          </details>
        ))}
        {overview?.events
          .slice(-50)
          .reverse()
          .map((event) => (
            <p key={event.sequence}>
              {event.kind}
              {event.reason ? ` · ${event.reason}` : ""}
            </p>
          ))}
      </section>
    );
  if (branch === "delivery") {
    const receipt =
      overview?.integration?.deliveryReceipt || run?.deliveryReceipt;
    return (
      <section className="m-card">
        <h2>交付结果</h2>
        {receipt ? (
          <>
            <p>状态：{receipt.status}</p>
            <p className="m-path">分支：{receipt.ref}</p>
            <p className="m-path">提交：{receipt.commit}</p>
            {receipt.integration ? <p>{receipt.integration}</p> : null}
            {receipt.publication ? (
              <p>{receipt.publication}</p>
            ) : (
              <p>Host 尚未报告远端发布记录。</p>
            )}
          </>
        ) : (
          <p>Host 尚未记录交付回执；对话中的完成说明不等于已交付。</p>
        )}
        {run?.reason ? <p className="m-prewrap">{run.reason}</p> : null}
      </section>
    );
  }
  return null;
}

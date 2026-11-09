import type { CurrentModel } from "../types/loom";
import type { ProjectOverview } from "../components/projectOverview";
import {
  useModelUsage,
  usageScopeText,
  usageWindowLabel,
  resetText,
} from "../components/ModelUsageIndicator";
import { ChevronRight, RotateCw } from "../components/icons";
import { BranchGroup, type BuildBranch } from "./BuildBranches";
import { Disclosure } from "./Disclosure";

function Quota({ selection, online }: { selection: string; online: boolean }) {
  const usage = useModelUsage(selection);
  const windows = usage.data?.windows ?? [];
  const balances = usage.data?.balances ?? [];
  const status =
    usage.data?.message ||
    (usage.loading
      ? "正在向供应商查询"
      : !windows.length && !balances.length
        ? "供应商没有返回可展示的额度数据"
        : "");
  return (
    <BranchGroup
      label="供应商额度"
      aside={
        <button
          type="button"
          className="m-icon-button"
          aria-label="刷新额度"
          disabled={!online || usage.loading}
          onClick={() => void usage.refresh(true)}
        >
          <RotateCw size={16} strokeWidth={1.75} spinning={usage.loading} />
        </button>
      }
    >
      <div className="m-card">
        {windows.map((row) => (
          <div className="m-quota" key={row.label}>
            <div className="m-quota-line">
              <span>{usageWindowLabel(row.label, true)}</span>
              <b>剩余 {row.remainingPercent}%</b>
            </div>
            <span
              className={`m-meter${row.remainingPercent < 15 ? " is-high" : ""}`}
              aria-hidden="true"
            >
              <i style={{ transform: `scaleX(${row.remainingPercent / 100})` }} />
            </span>
            <small>
              {row.resetsAt
                ? resetText(row.resetsAt, true)
                : "供应商未提供重置时间"}
            </small>
          </div>
        ))}
        {balances.length ? (
          <dl className="m-kv">
            {balances.map((row) => (
              <div key={row.currency}>
                <dt>{row.currency}</dt>
                <dd>{row.total}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {status ? <p role="status">{status}</p> : null}
        {usage.data?.stale ? (
          <p className="m-alert">当前显示上次成功查询的数据</p>
        ) : null}
      </div>
      <p className="m-note">
        {usageScopeText(usage.data, true)}与本次建造预算分别计算。
      </p>
    </BranchGroup>
  );
}

/** The parts of a branch that do not depend on the run's controls: read-only records, the model and its quota. */
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
  if (branch === "budget")
    return (
      <>
        <BranchGroup label="模型">
          <button
            type="button"
            className="m-card m-model-row"
            aria-label="调整模型、推理与对话权限"
            disabled={!online}
            onClick={onModelSettings}
          >
            <span>
              <strong>{model?.name || model?.model || "模型信息尚未就绪"}</strong>
              <small>
                {[model?.groupName || model?.adapter, `推理 ${model?.reasoning?.value || "模型默认"}`]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
            </span>
            <ChevronRight size={18} strokeWidth={1.75} />
          </button>
          <p className="m-note">建造的权限和并发在启动时固定，这里只影响新消息。</p>
        </BranchGroup>
        {model?.selection ? (
          <Quota key={model.selection} selection={model.selection} online={online} />
        ) : null}
      </>
    );
  const run = overview?.run;
  if (branch === "tasks") {
    const scopes = [
      ...new Set(run?.plan.flatMap((task) => task.fileScopes || []) || []),
    ];
    return scopes.length ? (
      <BranchGroup label="工作范围">
        <div className="m-card">
          <ul className="m-build-list">
            {scopes.map((path) => (
              <li className="m-path" key={path}>
                {path}
              </li>
            ))}
          </ul>
        </div>
        <p className="m-note">来自任务计划，不表示这些文件已经修改。</p>
      </BranchGroup>
    ) : null;
  }
  if (branch === "records") {
    const receipt =
      overview?.integration?.deliveryReceipt || run?.deliveryReceipt;
    const notes = run?.knowledge?.notes ?? [];
    const recoveries = Object.entries(run?.knowledge?.recoveries || {});
    const stages = overview?.program?.milestones ?? [];
    const events = overview?.events.slice(-50).reverse() ?? [];
    return (
      <>
        <BranchGroup label="交付">
          <div className="m-card">
            {receipt ? (
              <dl className="m-kv">
                <div><dt>状态</dt><dd>{receipt.status}</dd></div>
                <div><dt>分支</dt><dd className="m-path">{receipt.ref}</dd></div>
                <div><dt>提交</dt><dd className="m-path">{receipt.commit}</dd></div>
                {receipt.integration ? <div><dt>集成</dt><dd>{receipt.integration}</dd></div> : null}
                <div><dt>发布</dt><dd>{receipt.publication || "尚无远端发布记录"}</dd></div>
              </dl>
            ) : (
              <p>电脑确认交付后会显示在这里，对话里说完成不算交付。</p>
            )}
            {run?.reason ? <p className="m-prewrap">{run.reason}</p> : null}
          </div>
        </BranchGroup>
        <BranchGroup
          label="项目知识"
          aside={<span className="m-group-count">已验收 {run?.knowledge?.acceptedChecks ?? 0}</span>}
        >
          <div className="m-card">
            {notes.length ? (
              <ol className="m-build-list">
                {notes.map((note, index) => (
                  <li className="m-prewrap" key={index}>
                    {note}
                  </li>
                ))}
              </ol>
            ) : (
              <p>还没有记录项目笔记。</p>
            )}
            {recoveries.map(([kind, count]) => (
              <p key={kind}>
                {kind} · 自动恢复 {count} 次
              </p>
            ))}
          </div>
        </BranchGroup>
        <BranchGroup label="阶段与事件">
          <div className="m-card">
            {!stages.length && !events.length ? (
              <p>还没有建造阶段或运行事件。</p>
            ) : null}
            {stages.length ? (
              <div className="m-stages">
                {stages.map((stage) => (
                  <Disclosure
                    key={stage.index}
                    title={`阶段 ${stage.index}`}
                    aside={
                      <span className={`m-state${stage.complete ? " is-on" : ""}`}>
                        <i aria-hidden="true" />
                        {stage.complete ? "完成" : "未完成"}
                      </span>
                    }
                  >
                    <p className="m-note">
                      {stage.tokens.toLocaleString()} token · {stage.episodes} 轮
                    </p>
                    <p>{stage.summary}</p>
                  </Disclosure>
                ))}
              </div>
            ) : null}
            {events.length ? (
              <ul className="m-events">
                {events.map((event) => (
                  <li key={event.sequence}>
                    <span>{event.kind}</span>
                    {event.reason ? <small>{event.reason}</small> : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </BranchGroup>
      </>
    );
  }
  return null;
}

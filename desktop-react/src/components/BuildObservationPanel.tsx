import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "./icons";
import { buildStatus, type BuildBranch, type BuildInspection } from "./buildInspection";
import "./build-observation.css";

export function useBuildInspection(key: string, active: boolean, load?: () => Promise<BuildInspection>) {
  const [state, setState] = useState<{ key: string; data?: BuildInspection; error?: string; loading: boolean }>({ key, loading: false });
  const loader = useRef(load);
  loader.current = load;
  const available = Boolean(load);
  const ticket = useRef(0);
  const inFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (!loader.current || inFlight.current) return;
    inFlight.current = true;
    const current = ++ticket.current;
    setState(old => ({ key, data: old.key === key ? old.data : undefined, loading: true }));
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const data = await Promise.race([loader.current(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Refresh timed out")), 20000);
      })]);
      if (current === ticket.current) setState({ key, data, loading: false });
    } catch (error) {
      if (current === ticket.current) setState(old => ({ ...old, key, error: error instanceof Error ? error.message : String(error), loading: false }));
    } finally { clearTimeout(timer); if (current === ticket.current) inFlight.current = false; }
  }, [key]);
  useEffect(() => {
    ++ticket.current; inFlight.current = false;
    if (!active || !available) {
      if (!available) setState({ key, loading: false });
      return;
    }
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 12000);
    return () => { clearInterval(timer); ++ticket.current; inFlight.current = false; };
  }, [key, active, available, refresh]);
  return { ...(state.key === key ? state : { data: undefined, error: undefined, loading: active && available }), refresh };
}

function number(value: number) { return value.toLocaleString(); }
function duration(seconds: number, en: boolean) {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  return minutes < 1 ? (en ? "<1 min" : "不到 1 分钟") : minutes < 60 ? `${minutes} ${en ? "min" : "分钟"}` : `${Math.floor(minutes / 60)} ${en ? "h" : "小时"} ${minutes % 60} ${en ? "min" : "分"}`;
}
function at(seconds?: number, en = false) { return seconds ? new Date(seconds * 1000).toLocaleTimeString(en ? "en-US" : "zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"; }
function Badge({ value, en }: { value: string; en: boolean }) {
  return <span className="bo-badge" data-status={value}>{buildStatus(value, en)}</span>;
}

export function BuildObservationPanel({ branch, data, loading, error, offline, refresh, lang = "zh", onAttention, attentionLabel, busy }: {
  branch: BuildBranch; data?: BuildInspection; loading: boolean; error?: string; offline?: boolean;
  refresh(): unknown; lang?: "zh" | "en"; onAttention?(): void; attentionLabel?: string; busy?: boolean;
}) {
  const en = lang === "en";
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(timer); }, []);
  const stale = Boolean(data && now / 1000 - data.observedAt > 30);
  const text = (zh: string, english: string) => en ? english : zh;
  const empty = (zh: string, english: string) => <p className="bo-empty">{text(zh, english)}</p>;
  const goal = data?.goal;
  const lines = goal?.lines || [];
  const waitLabels: Record<string, [string, string, string, string]> = {
    workerCap: ["等待开发名额", "Waiting for a worker slot", "有开发代理结束后自动调度", "Resumes when a worker slot becomes free"],
    fileScope: ["文件或资源占用", "Files or resources occupied", "相关任务合并或释放资源后继续", "Resumes when the conflicting work releases its scope"],
    admission: ["等待 Host 执行名额", "Waiting for Host capacity", "其他执行结束后自动调度", "Resumes when Host capacity is free"],
    reviewerCap: ["等待复审名额", "Waiting for a reviewer", "复审名额释放后继续", "Resumes when a reviewer becomes free"],
    disk: ["磁盘空间不足", "Insufficient disk space", "释放工作盘空间后重新调度", "Free workspace disk space to allow scheduling"],
    provider: ["模型服务重试", "Provider retry", "到重试时间后自动继续", "Resumes at the recorded retry time"],
    credential_blocked: ["模型凭证不可用", "Provider credential blocked", "更新凭证后继续", "Update the provider credential, then resume"],
    budget_limited: ["预算已到上限", "Budget exhausted", "追加预算后继续", "Increase the budget to resume"],
    inspection: ["等待检查", "Waiting for inspection", "查看复审问题后处理", "Inspect the review issues before continuing"],
    approval: ["等待你批准", "Waiting for your approval", "在批准请求处回应", "Respond to the approval request"],
    paused: ["已暂停", "Paused", "继续项目后恢复调度", "Resume the project to continue scheduling"],
  };
  return <div className="bo-panel" data-observation={branch}>
    <div className="bo-freshness" role="status" data-stale={offline || stale || Boolean(error) || undefined}>
      <span>{offline ? text("连接中断 · 保留上次记录", "Offline · showing last snapshot") : error ? text("刷新失败 · 保留上次记录", "Refresh failed · showing last snapshot")
        : stale ? text("记录已过期", "Snapshot is stale") : loading ? text("正在读取…", "Reading…") : text("运行记录", "Recorded state")}
        {data ? <time dateTime={new Date(data.observedAt * 1000).toISOString()}> · {at(data.observedAt, en)}</time> : null}</span>
      <button type="button" aria-label={text("刷新运行记录", "Refresh build records")} disabled={loading} onClick={() => { void refresh(); }}><RefreshCw size={12} /></button>
    </div>
    {error ? <p className="bo-error" role="alert">{error}</p> : null}
    {!data ? empty(loading ? "正在读取工作状态与证据…" : "还没有读取到运行记录。", loading ? "Reading build state and evidence…" : "No build snapshot is available.") : <>
      {branch === "blockers" ? <>
        {data.waits.map(wait => <article className="bo-entry" key={wait.id}>
          <strong>{waitLabels[wait.code]?.[en ? 1 : 0] || wait.code}</strong>
          <small>{data.tasks.filter(task => task.runId === wait.runId).map(task => task.id).join(" · ") || (wait.runId === data.runId ? text("整个项目", "Whole project") : wait.runId.slice(0, 8))}</small>
          {wait.detail ? <p>{wait.detail}</p> : null}
          <small>{waitLabels[wait.code]?.[en ? 3 : 2]}</small>
          <small>{wait.since ? `${text("开始", "Since")} ${at(wait.since, en)} · ${duration(now / 1000 - wait.since, en)}` : null}
            {!wait.since && wait.recordedAt ? `${text("记录于", "Recorded at")} ${at(wait.recordedAt, en)}` : null}
            {wait.retryAt ? ` · ${text("重试", "Retry")} ${at(wait.retryAt, en)}` : null}</small>
        </article>)}
        {goal && (goal.retryAt || goal.status !== "active" || goal.phase === "approval") ? <article className="bo-entry"><strong>{goal.phase === "approval" ? text("等待批准", "Approval required") : goal.retryAt ? text("等待模型重试", "Waiting for provider retry") : buildStatus(goal.status, en)}</strong><p>{goal.reason}</p>{goal.retryAt ? <small>{text("重试时间", "Retry at")} {at(goal.retryAt, en)}</small> : null}</article> : null}
        {lines.filter(line => line.state === "waiting" || line.state === "failed").map((line, index) => <article className="bo-entry" key={index}><strong>{line.title}</strong><Badge value={line.state} en={en} /><p>{line.why}</p>{line.retryAt ? <small>{text("重试时间", "Retry at")} {at(line.retryAt, en)}</small> : null}</article>)}
        {!data.waits.length && !goal?.retryAt && goal?.phase !== "approval" && (!goal || goal.status === "active") && !lines.some(line => ["waiting", "failed"].includes(line.state)) ? empty("当前未记录阻塞。任务依赖见下方。", "No blockers recorded. Task dependencies are listed below.") : null}
        {data.tasks.filter(task => task.dependsOn.length).map(task => <details className="bo-entry" key={task.id}><summary>{task.id} · {text("依赖", "Depends on")} {task.dependsOn.join(", ")}</summary><p>{task.title}</p>{task.dependsOn.map(id => <p key={id}><code>{id}</code> <Badge value={data.tasks.find(t => t.id === id)?.state || "unknown"} en={en} /></p>)}</details>)}
        {onAttention ? <button className="bo-action" disabled={!data || loading || busy || offline || stale || Boolean(error)} onClick={onAttention}>{attentionLabel || text("处理当前等待事项", "Resolve current attention item")}</button> : null}
      </> : null}
      {branch === "evidence" ? <>
        {data.freshness ? <p className="bo-note">{text("当前候选：", "Current candidate: ")}<Badge value={data.freshness} en={en} /></p> : null}
        {data.criteria.map(criterion => <details className="bo-entry" key={criterion.id}><summary><span>{criterion.expectation}</span><Badge value={criterion.status} en={en} /></summary>
          <small>{criterion.id}</small>{criterion.evidence ? <><p>{criterion.evidence.observed || text("执行详情未保留", "Execution details were not retained")}</p>
            {criterion.evidence.command ? <pre>{criterion.evidence.command}</pre> : null}
            {criterion.evidence.log ? <details className="bo-log"><summary>{text("执行日志（最多末尾 8,000 字符）", "Execution log (last 8,000 characters)")}</summary><pre>{criterion.evidence.log}</pre></details> : null}
            <dl><dt>{text("记录时间", "Recorded at")}</dt><dd>{criterion.evidence.recordedAt ? new Date(criterion.evidence.recordedAt).toLocaleString(en ? "en-US" : "zh-CN") : "—"}</dd><dt>{text("退出码", "Exit code")}</dt><dd>{criterion.evidence.exitCode ?? text("未记录", "Not recorded")}</dd><dt>{text("事件", "Event")}</dt><dd>{criterion.evidence.eventId}</dd>
              <dt>{text("候选", "Candidate")}</dt><dd>{criterion.evidence.candidateId}</dd><dt>{text("检查来源", "Check source")}</dt><dd>{criterion.evidence.source || "—"}</dd></dl>
            {criterion.evidence.subjectPaths.map(path => <code className="bo-path" key={path}>{path}</code>)}</> : <p>{text("尚无可引用的独立验收执行证据。", "No independent execution evidence has been cited.")}</p>}</details>)}
        {goal ? <article className="bo-entry"><strong>{text("长目标独立检查", "Goal independent check")}</strong><Badge value={goal.checked ? "recorded_pass" : goal.phase === "checking" ? "running" : "not_run"} en={en} /><p>{goal.summary || text("尚未记录检查结论", "No check conclusion recorded")}</p></article> : null}
        {lines.map((line, index) => <article className="bo-entry" key={index}><strong>{line.title}</strong><Badge value={line.check || "not_run"} en={en} /><p>{line.why}</p></article>)}
        {!data.criteria.length && !goal ? empty("计划尚未定义验收条件。", "No acceptance criteria have been defined.") : null}
        <p className="bo-note">{text("历史通过只证明对应候选版本；未记录执行不代表检查通过。", "A historical pass applies to its recorded candidate. Missing execution is not a pass.")}</p>
      </> : null}
      {branch === "reviews" ? <>
        {data.reviews.map(review => <details className="bo-entry" key={review.id}><summary><span>{buildStatus(review.scope, en)}</span><Badge value={review.verdict} en={en} /></summary><p>{review.summary}</p>
          {review.issues.map((issue, index) => <p className="bo-issue" key={index}><code>{issue.taskId}</code> {issue.description}</p>)}
          <small>{text("执行证据", "Evidence references")} · {review.checks.length}</small><small>{text("运行", "Run")} · {review.runId}</small><code className="bo-path">{review.candidateId}</code></details>)}
        {Object.entries(data.recoveries || {}).map(([kind, count]) => <div className="bo-row" key={kind}><span>{buildStatus(kind, en)}</span><span>{count}</span></div>)}
        {!data.reviews.length ? empty(goal?.phase === "checking" ? "独立检查正在进行，尚无项目复审报告。" : "尚无项目复审报告。", goal?.phase === "checking" ? "Independent checking is running; no project review report yet." : "No project review reports recorded.") : null}
        <p className="bo-note">{text("报告按各运行分组，组内最新优先。", "Reports are grouped by run, newest first within each run.")}</p>
      </> : null}
      {branch === "changes" ? <>
        {data.changes.map(change => <details className="bo-entry" key={change.runId} open={data.changes.length === 1}><summary><code>{change.runId.slice(0, 8)}</code><span>{change.total} {text("个文件", "files")}</span></summary>
          <small>{text("对比起点提交", "Compared with base commit")} <code>{change.baseCommit.slice(0, 12) || "—"}</code></small>
          {change.error ? <p className="bo-error">{change.error === "checkout_unavailable" ? text("工作副本尚未建立或已清理，无法读取文件差异。", "The checkout is not available; file changes cannot be read.") : change.error}</p> : null}
          {change.files.map(file => <div className="bo-file" key={file.path}><b>{file.status}</b><code>{file.path}</code></div>)}
          {change.total > change.files.length ? <small>{text("仅显示前", "Showing first")} {change.files.length} / {change.total}</small> : null}
          {!change.error && !change.total ? <p>{text("该工作副本没有相对起点的文件改动。", "No file changes from this checkout's base commit.")}</p> : null}</details>)}
        {data.tasks.map(task => <details className="bo-entry" key={task.id}><summary><span>{task.id}</span><Badge value={task.state} en={en} /></summary><p>{task.title}</p><small>{text("工作运行", "Work run")} {task.runId}</small>{task.fileScopes.map(path => <code className="bo-path" key={path}>{path}</code>)}</details>)}
        {lines.map((line, index) => <details className="bo-entry" key={index}><summary>{line.title}<Badge value={line.state} en={en} /></summary>{line.changed?.map(path => <code className="bo-path" key={path}>{path}</code>)}<p>{line.report || text("尚无子任务报告", "No line report yet")}</p></details>)}
        {!data.changes.length && !lines.length ? empty("尚无可读取的工作副本或文件改动记录。", "No checkout or recorded file changes are available.") : null}
      </> : null}
      {branch === "spending" ? <>
        {(data.budget || goal) ? (() => { const used = data.budget ? data.budget.used + data.budget.earlierMilestones : goal!.tokens; const limit = data.budget?.limit ?? goal!.tokenBudget;
          return <div className="bo-total"><strong>{number(used)} <small>/ {number(limit)} token</small></strong><meter min={0} max={Math.max(1, limit, used)} value={used} aria-label={text("已用预算", "Budget used")} /><span>{text("剩余额度", "Remaining budget")} {number(Math.max(0, limit - used))}</span></div>; })() : empty("尚无预算记录。", "No budget recorded.")}
        {data.budget && goal ? <div className="bo-entry"><strong>{text("长目标预算（单独计量）", "Goal budget (separate ledger)")}</strong><p>{number(goal.tokens)} / {number(goal.tokenBudget)} token</p></div> : null}
        {Object.entries(data.metrics?.phases || {}).map(([phase, value]) => <div className="bo-entry" key={phase}><div className="bo-row"><strong>{buildStatus(phase, en)}</strong><span>{number(value.tokens)} token</span></div><small>{value.episodes} {text("轮", "episodes")} · {duration(value.seconds, en)}</small></div>)}
        {data.budget?.position ? <dl><dt>{text("已知用量", "Known tokens")}</dt><dd>{number(data.budget.position.knownTokens)}</dd><dt>{text("执行中预留", "In-flight reservation")}</dt><dd>{number(data.budget.position.inFlightTokens)}</dd><dt>{text("用量未知的请求", "Requests with unknown usage")}</dt><dd>{data.budget.position.unknownRequests}</dd></dl> : null}
        {Object.entries(data.metrics?.waits || {}).map(([code, seconds]) => <div className="bo-row" key={code}><span>{waitLabels[code]?.[en ? 1 : 0] || code}</span><span>{duration(seconds!, en)}</span></div>)}
        {lines.map((line, index) => <div className="bo-row" key={index}><span>{line.title}</span><span>{number(line.tokens || 0)} / {number(line.cap || 0)}</span></div>)}
        <p className="bo-note">{text("阶段明细仅覆盖当前阶段的执行记录；等待耗时可重叠。额度在请求边界约束，不是精确账单。", "Phase details cover this milestone; wait durations may overlap. Budget enforcement is at request boundaries, not a billing statement.")}</p>
      </> : null}
      {branch === "delivery" ? <>
        {data.deliveries.map(delivery => <article className="bo-entry" key={delivery.runId}><div className="bo-row"><code>{delivery.runId.slice(0, 8)}</code><Badge value={delivery.state} en={en} /></div>
          <dl><dt>{text("验收", "Verification")}</dt><dd><Badge value={delivery.verification} en={en} /></dd><dt>{text("候选交付", "Candidate delivery")}</dt><dd><Badge value={delivery.receipt?.status || "pending"} en={en} /></dd>
            <dt>{text("本地集成", "Local integration")}</dt><dd><Badge value={delivery.receipt?.integration || delivery.integration || "pending"} en={en} /></dd><dt>{text("远端发布", "Remote publication")}</dt><dd><Badge value={delivery.receipt?.publication || "unknown"} en={en} /></dd></dl>
          {delivery.receipt?.commit ? <code className="bo-path">{delivery.receipt.commit}</code> : null}<code className="bo-path">{delivery.receipt?.ref || delivery.branch}</code>
          {delivery.reason ? <p>{delivery.reason}</p> : null}</article>)}
        {goal ? <article className="bo-entry"><strong>{text("长目标结果", "Goal result")}</strong><Badge value={goal.status === "complete" && goal.checked ? "recorded_pass" : goal.status} en={en} /><p>{goal.summary || goal.reason || text("尚无交付结论", "No delivery conclusion recorded")}</p></article> : null}
        {!data.deliveries.length && !goal ? empty("尚无交付记录。", "No delivery records.") : null}
        <div className="bo-entry"><div className="bo-row"><span>{text("构建", "Build")}</span><Badge value="unknown" en={en} /></div><div className="bo-row"><span>{text("部署", "Deployment")}</span><Badge value="unknown" en={en} /></div><div className="bo-row"><span>{text("服务运行", "Service runtime")}</span><Badge value="unknown" en={en} /></div></div>
        <p className="bo-note">{text("当前运行器未记录构建、部署和服务健康状态；本地集成不能证明远端发布或服务可用。", "The runner does not record build, deployment or service health. Local integration does not establish publication or availability.")}</p>
      </> : null}
    </>}
  </div>;
}

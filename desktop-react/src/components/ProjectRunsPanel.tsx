import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { actions, budgetValues, effectiveRun, ProjectCommand, type ProjectAction, type ProjectAssessment, type ProjectRun } from "./projectRuns";
import "./project-runs.css";

type Thread = { id: string; title: string; workspace: string };
const labels: Record<string, [string, string]> = {
  created: ["待启动", "Ready to start"], running: ["执行中", "Running"], paused: ["已暂停", "Paused"],
  cancelled: ["已取消", "Cancelled"], needs_review: ["需要检查", "Needs inspection"],
  ready_for_review: ["候选成果", "Candidate ready"], delivered: ["已交付本地分支", "Local branch delivered"],
  integrated: ["已集成到本地", "Integrated locally"], planning: ["拆解任务", "Planning"], working: ["实现任务", "Implementing"],
  verifying: ["独立验收", "Independent review"], blocked: ["等待处理", "Needs attention"], completed: ["已完成", "Completed"],
  budget_limited: ["预算已用尽", "Budget exhausted"], waiting_approval: ["等待批准", "Awaiting approval"],
  outcome_unknown: ["执行结果未确认", "Outcome unknown"], pending: ["待执行", "Pending"], verified: ["验收通过", "Verified"],
  candidate: ["待验收", "Awaiting review"], passed: ["通过", "Passed"], needs_changes: ["需要修改", "Needs changes"],
  stale: ["已过期", "Stale"], continue: ["继续检查", "Continue checking"], not_assessed: ["未验收", "Not assessed"],
  current: ["当前有效", "Current"], unavailable: ["无法确认", "Unavailable"], task: ["单项任务", "Task"], project: ["完整项目", "Whole project"],
  integration_review: ["合并验收中", "Reviewing integration"], deliver: ["交付成果", "Delivering"],
  parallel: ["并行开发", "Parallel implementation"],
  prepare: ["准备合并", "Preparing integration"], apply: ["集成成果", "Applying integration"], prepared: ["准备执行", "Prepared"],
};
const actionLabels: Record<ProjectAction, [string, string]> = {
  start: ["启动", "Start"], pause: ["暂停", "Pause"], resume: ["继续", "Resume"], cancel: ["取消任务", "Cancel task"],
  deliver: ["交付本地分支", "Deliver local branch"], review: ["重新独立验收", "Review again"],
  prepareIntegration: ["准备合并并重新验收", "Merge and review"], applyIntegration: ["集成到源分支", "Integrate into source"],
};
export function ProjectRunsPanel({ threadId, onOpenThread }: { threadId?: string | null; onOpenThread?(id: string): Promise<void> }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const copy = (cn: string, en: string) => zh ? cn : en;
  const label = (key: string) => labels[key]?.[zh ? 0 : 1] || key;
  const [runs, setRuns] = useState<ProjectRun[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selected, setSelected] = useState("");
  const [source, setSource] = useState(threadId || "");
  const [objective, setObjective] = useState("");
  const [tokens, setTokens] = useState("100000");
  const [episodes, setEpisodes] = useState("100");
  const [automatic, setAutomatic] = useState(false);
  const [workers, setWorkers] = useState(1);
  const [budgetTokens, setBudgetTokens] = useState("");
  const [budgetEpisodes, setBudgetEpisodes] = useState("");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("");
  const [schedulerError, setSchedulerError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [assessments, setAssessments] = useState<ProjectAssessment[] | null>(null);
  const [assessmentRun, setAssessmentRun] = useState("");
  const [freshness, setFreshness] = useState<{ id: string; revision: number; value: string } | null>(null);
  const alive = useRef(true);
  const serial = useRef(0);
  const command = useRef(new ProjectCommand((method, params) => window.loom.call(method, params)));
  async function refresh() {
    const seq = ++serial.current;
    const result = await window.loom.call<{ runs: ProjectRun[]; error?: string }>("projectRun/list", {});
    if (alive.current && seq === serial.current) {
      setRuns(result.runs); setSchedulerError(result.error || ""); setConnection(""); setLoaded(true);
    }
  }
  useEffect(() => {
    alive.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(); } catch (e) { if (!stopped) setConnection(String(e)); }
      if (!stopped) timer = setTimeout(() => void poll(), 3000);
    };
    void poll();
    void window.loom.call<{ threads: Thread[] }>("thread/list", {}).then(result => {
      if (!stopped) setThreads(result.threads);
    }).catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped = true; alive.current = false; ++serial.current; clearTimeout(timer); };
  }, []);
  const run = runs.find(item => item.id === selected);
  const current = run ? effectiveRun(run, runs) : undefined;
  useEffect(() => {
    setBudgetTokens(String(current?.tokenBudget || "")); setBudgetEpisodes(String(current?.maxEpisodes || ""));
  }, [selected, current?.id, current?.tokenBudget, current?.maxEpisodes]);
  useEffect(() => { setAssessments(null); setFreshness(null); }, [selected]);
  const pending = command.current.pending;
  async function mutate(action?: ProjectAction | "create") {
    if (busy) return;
    setBusy(true); setError("");
    try {
      let result: ProjectRun;
      if (!action) result = await command.current.send();
      else if (action === "create") result = await command.current.send("projectRun/create", {
        threadId: source, objective: objective.trim(), ...budgetValues(tokens, episodes),
        reviewPolicy: "independent", completionPolicy: automatic ? "local_integrate" : "manual",
        maxWorkers: workers,
      });
      else {
        if (!run) return;
        let extra: Record<string, number> = {};
        if ((!run.teamOwnerId && ["review", "prepareIntegration"].includes(action)) || (action === "resume" && (run.completionPolicy === "local_integrate" || (run.maxWorkers || 1) > 1))) {
          const values = budgetValues(budgetTokens, budgetEpisodes);
          extra = action === "resume" ? Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== current?.[key as "tokenBudget" | "maxEpisodes"])) : values;
        }
        result = await command.current.send(`projectRun/${action}`, { runId: run.id, expectedRevision: run.revision, ...extra });
      }
      if (alive.current) {
        setRuns(old => [...old.filter(item => item.id !== result.id), result]); setSelected(result.id);
        setAssessments(null); setFreshness(null);
      }
      await refresh();
    } catch (e) { if (alive.current) setError(String(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function inspect() {
    if (!run) return;
    const id = current?.id || run.id;
    setBusy(true); setError("");
    try {
      const [read, checks] = await Promise.all([
        window.loom.call<{ run: ProjectRun }>("projectRun/get", { runId: id }),
        window.loom.call<{ assessments: ProjectAssessment[] }>("projectRun/assessments", { runId: id }),
      ]);
      if (alive.current) {
        setAssessmentRun(id); setAssessments(checks.assessments);
        setFreshness({ id, revision: read.run.revision, value: read.run.verificationFreshness || "not_assessed" });
      }
    } catch (e) { if (alive.current) setError(String(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  const disabled = busy || Boolean(pending) || Boolean(connection) || !loaded;
  const status = (item: ProjectRun) => item.workflowStatus === "completed" ? "integrated" : item.workflowStatus === "blocked" ? "blocked"
    : item.completionPolicy === "local_integrate" && item.workflowControl === "paused" && item.state !== "created" ? "paused"
    : item.completionPolicy === "local_integrate" && ["deliver", "prepare", "apply"].includes(item.workflowStatus || "") ? item.workflowStatus!
    : item.integrationRunId && item.workflowControl === "active" ? "integration_review"
    : item.teamOwnerId && !item.activeEpisode && runs.find(parent => parent.id === item.teamOwnerId)?.control === "paused" ? "paused"
    : item.activeEpisode?.state === "waiting_approval" ? "waiting_approval" : item.activeEpisode?.state === "outcome_unknown" ? "outcome_unknown"
    : item.control === "budget_limited" ? "budget_limited" : item.state === "running" && item.control === "paused" ? "paused" : item.state;
  return <section className="project-runs" aria-label={copy("持久项目", "Persistent projects")}>
    <header><p className="pr-eyebrow">{copy("持续推进大型任务", "LONG-RUNNING WORK")}</p><h1>{copy("持久项目", "Persistent projects")}</h1>
      <p>{copy("分步实现、独立验收，保存进度后继续。关闭此页面不会停止 Host 中的任务。", "Implement in steps, verify independently, and retain progress. Closing this page does not stop Host tasks.")}</p></header>
    {(error || connection || schedulerError) && <div className="pr-error" role="alert">{error || connection || schedulerError}</div>}
    {pending && <aside className="pr-notice"><p>{copy("上次请求尚待确认。重试会沿用同一编号，不会另建一份任务。", "The previous request needs confirmation. Retry uses the same operation ID.")}</p>
      <button disabled={busy} onClick={() => void mutate()}>{copy("重试上次请求", "Retry request")}</button>
      <button disabled={busy} onClick={() => { void refresh().then(() => { command.current.discard(); setError(""); }).catch(e => setError(String(e))); }}>{copy("读取最新状态并结束重试", "Refresh and end retry")}</button></aside>}
    <details className="pr-create" open={!runs.length}>
      <summary>{copy("创建持久任务", "Create a persistent task")}</summary>
      <form onSubmit={e => { e.preventDefault(); if (!disabled) void mutate("create"); }}>
        <fieldset disabled={disabled}>
          <label>{copy("沿用会话的工作区、模型与权限", "Use a conversation’s workspace, model and permissions")}<select required value={source} onChange={e => setSource(e.target.value)}>
            <option value="">{copy("选择会话", "Choose a conversation")}</option>{threads.map(item => <option key={item.id} value={item.id}>{item.title || item.id} — {item.workspace}</option>)}</select></label>
          <label>{copy("目标与验收要求", "Objective and acceptance requirements")}<textarea required maxLength={20000} rows={4} value={objective} onChange={e => setObjective(e.target.value)} /></label>
          <div className="pr-pair"><label>{copy("总 token 预算", "Total token budget")}<input type="number" min={1} max={1e9} step={1} required value={tokens} onChange={e => setTokens(e.target.value)} /></label>
            <label>{copy("最多执行轮次", "Maximum episodes")}<input type="number" min={1} max={10000} step={1} required value={episodes} onChange={e => setEpisodes(e.target.value)} /></label></div>
          <label className="pr-check"><input type="checkbox" checked={automatic} onChange={e => setAutomatic(e.target.checked)} />{copy("验收通过后自动集成到本地源分支", "Automatically integrate into the local source branch after acceptance")}</label>
          <label>{copy("并行执行上限", "Concurrency limit")}<select value={workers} onChange={e => setWorkers(Number(e.target.value))}><option value={1}>{copy("1 · 逐项执行", "1 · Sequential")}</option><option value={2}>{copy("2 · 独立任务可并行", "2 · Parallel independent tasks")}</option></select></label>
          <p className="pr-muted">{copy("有依赖或共享资源的任务仍会串行。并行任务共用总预算。", "Dependencies and shared resources remain serialized. Parallel tasks share the total budget.")}</p>
          <p className="pr-muted">{copy("需要已提交且干净的 Git 工作区。自动模式会更新本地源分支，不会推送或发布。创建后仍需点击启动。", "Requires a clean, committed Git workspace. Automatic mode updates the local source branch without pushing or publishing. Start the task after creating it.")}</p>
          <button type="submit" disabled={!source || !objective.trim()}>{copy("创建，暂不执行", "Create without starting")}</button>
        </fieldset>
      </form>
    </details>
    <div className="pr-workspace"><nav aria-label={copy("项目任务", "Project tasks")}>
      <div className="pr-list-heading"><h2>{copy("任务", "Tasks")}</h2><button disabled={busy} onClick={() => void refresh().catch(e => setConnection(String(e)))}>{copy("刷新", "Refresh")}</button></div>
      {!loaded && <p role="status">{copy("正在读取…", "Loading…")}</p>}
      {loaded && !runs.length && <p className="pr-muted">{copy("还没有持久任务。创建后可在这里查看真实进度。", "No persistent tasks yet. Create one to follow its progress here.")}</p>}
      {runs.map(item => <button className="pr-run" key={item.id} aria-current={selected === item.id ? "true" : undefined} onClick={() => setSelected(item.id)}>
        <span>{item.objective}</span><small>{item.teamOwnerId ? copy("协作任务 · ", "Team task · ") : item.integrationTarget ? copy("合并验收 · ", "Integration review · ") : ""}{label(status(item))}</small></button>)}
    </nav><article aria-busy={busy}>
      {!run || !current ? <p className="pr-muted">{copy("选择任务查看步骤、验收和用量。", "Choose a task to inspect steps, acceptance and usage.")}</p> : <>
        <div className="pr-detail-heading"><h2>{run.objective}</h2><span className="pr-status">{label(status(run))}</span></div>
        <p className="pr-path">{run.repository}</p>
        {onOpenThread && <button disabled={busy} onClick={() => void onOpenThread(run.sourceThreadId).catch(e => setError(String(e)))}>{copy("打开源会话", "Open source conversation")}</button>}
        <p>{copy("当前阶段：", "Current phase: ")}{label(current.phase)}{current.id !== run.id ? copy(" · 合并结果重新验收", " · Reviewing merged content") : ""}</p>
        {run.integrationRunId && <button onClick={() => setSelected(run.integrationRunId!)}>{copy("查看合并验收任务", "View integration review")}</button>}
        {run.workflowOwnerId && <p>{copy("此任务由主任务统一控制。", "This task is controlled by its parent.")} <button onClick={() => setSelected(run.workflowOwnerId!)}>{copy("返回主任务", "Back to parent")}</button></p>}
        {run.teamOwnerId && <p>{copy("协作任务的预算与暂停由主任务统一控制。", "The parent owns this task’s budget and pause controls.")} <button onClick={() => setSelected(run.teamOwnerId!)}>{copy("返回主任务", "Back to parent")}</button></p>}
        {run.teamChildren?.length ? <div className="pr-actions">{run.teamChildren.map(item => <button key={item.runId} onClick={() => setSelected(item.runId)}>{item.taskId} · {item.state === "merged" ? copy("已合并", "Merged") : label(status(runs.find(child => child.id === item.runId) || run))}</button>)}</div> : null}
        {(run.workflowError || current.reason || run.reason) && <p className="pr-notice">{run.workflowError || current.reason || run.reason}</p>}
        <dl className="pr-metrics"><div><dt>{copy("累计 token", "Cumulative tokens")}</dt><dd>{current.tokensUsed.toLocaleString()} / {current.tokenBudget.toLocaleString()}</dd></div>
          <div><dt>{copy("执行轮次", "Episodes")}</dt><dd>{current.episodesUsed} / {current.maxEpisodes}</dd></div>
          <div><dt>{copy("已实现步骤", "Implemented steps")}</dt><dd>{current.plan.filter(item => ["verified", "candidate"].includes(item.state)).length} / {current.plan.length}</dd></div>
          <div><dt>{copy("完整验收", "Whole-project acceptance")}</dt><dd>{current.verificationStatus === "passed" ? label("passed") : copy("尚未通过", "Not yet passed")}</dd></div></dl>
        {(run.maxWorkers || 1) > 1 && <p className="pr-muted">{copy("协作任务当前执行数：", "Active team tasks: ")}{runs.filter(item => item.teamOwnerId === run.id && item.activeEpisode && item.activeEpisode.state !== "outcome_unknown").length} / {run.maxWorkers}</p>}
        <p className="pr-muted">{copy("token 限额在执行轮次之间检查，单轮可能超出。空闲、查看状态不会调用模型；电脑睡眠或 Host 退出时不执行。", "Token limits are checked between episodes; one episode may exceed the limit. Idle/status checks do not call a model. Tasks do not execute while the computer sleeps or Host is stopped.")}</p>
        {!run.teamOwnerId && <details><summary>{copy("总预算与追加额度", "Total budgets and explicit increases")}</summary><div className="pr-pair">
          <label>{copy("总 token 上限", "Total token limit")}<input type="number" min={1} max={1e9} step={1} value={budgetTokens} onChange={e => setBudgetTokens(e.target.value)} /></label>
          <label>{copy("总执行轮次上限", "Total episode limit")}<input type="number" min={1} max={10000} step={1} value={budgetEpisodes} onChange={e => setBudgetEpisodes(e.target.value)} /></label></div>
          <p className="pr-muted">{copy("填写总上限，并通过继续或重新验收提交；不是额外增加的数量。", "Enter total limits, then submit via Resume or Review again; these are not incremental amounts.")}</p></details>}
        <div className="pr-actions">{actions(run, current.id !== run.id ? current : undefined).map(action => <button key={action} disabled={disabled} onClick={() => void mutate(action)}>{actionLabels[action][zh ? 0 : 1]}</button>)}</div>
        <ol className="pr-plan">{current.plan.map(task => <li key={task.id}><div><strong>{task.instructions}</strong><span>{label(task.state)}</span></div>
          {task.dependsOn?.length ? <p className="pr-muted">{copy("依赖：", "Depends on: ")}{task.dependsOn.join(", ")}</p> : null}
          {task.fileScopes?.length ? <p className="pr-muted">{copy("文件范围：", "File scope: ")}{task.fileScopes.join(", ")}</p> : null}
          <ul>{task.acceptance.map((text, index) => <li key={index}>{text}</li>)}</ul></li>)}</ol>
        {current.activeEpisode && <div><p className="pr-muted">{copy("执行会话：", "Execution session: ")}{current.activeEpisode.sessionId} · {label(current.activeEpisode.state)}</p>
          {onOpenThread && <button disabled={busy} onClick={() => void onOpenThread(current.activeEpisode!.sessionId).catch(e => setError(String(e)))}>{copy("打开执行会话 / 处理审批", "Open execution / handle approval")}</button>}</div>}
        <button disabled={busy} onClick={() => void inspect()}>{copy("读取验收证据与有效性", "Read acceptance evidence and freshness")}</button>
        {freshness?.id === current.id && <p>{copy("验收有效性：", "Acceptance freshness: ")}{freshness.revision === current.revision ? label(freshness.value) : copy("状态已更新，请重新读取", "Status changed; read again")}</p>}
        {assessments && assessmentRun === current.id && <div className="pr-assessments">{!assessments.length && <p>{copy("尚无独立验收记录。", "No independent assessments yet.")}</p>}{assessments.map(item => <details key={item.id}><summary>{label(item.verdict)} · {label(item.scope)}</summary><p>{item.summary}</p><ul>{item.issues.map((issue, index) => <li key={index}>{issue.taskId}: {issue.description}</li>)}</ul>{item.checks.map(check => <p className="pr-muted" key={check.eventId}>{check.criterionId} · {check.eventId}</p>)}</details>)}</div>}
        {current.deliveryReceipt && <div className="pr-receipt"><h3>{copy("本地交付", "Local delivery")}</h3><code>{current.deliveryReceipt.ref}</code><code>{current.deliveryReceipt.commit}</code><p>{copy("远端发布未执行。", "Remote publication has not been performed.")}</p></div>}
      </>}
    </article></div>
  </section>;
}

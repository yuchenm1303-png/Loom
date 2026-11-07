import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Blocks,
  Bot,
  ChevronRight,
  FileText,
  GitBranch,
  ListChecks,
  MessageSquareText,
  RotateCw,
  ShieldCheck,
  Compass,
} from "../components/icons";
import {
  toFlowData,
  toGoal,
  raisedBudget,
  type ProjectOverview,
} from "../components/projectOverview";
import { withProjectReadTimeout } from "../state/projectRead";
import { confirmBiometric } from "./mobileBridge";
import { Disclosure } from "./Disclosure";
import {
  buildStartParams,
  resumeBudgetParams,
  type BuildSettings,
} from "./mobileProjectState";

const GLYPH = { size: 19, strokeWidth: 1.75 } as const;

function Section({
  title,
  icon,
  tone,
  children,
}: {
  title: string;
  icon?: ReactNode;
  tone?: "wait";
  children: ReactNode;
}) {
  return (
    <section className={`m-card${tone ? ` is-${tone}` : ""}`}>
      <div className="m-card-head">
        {icon ? (
          <span className={`m-tile${tone ? ` is-${tone}` : ""}`} aria-hidden="true">
            {icon}
          </span>
        ) : null}
        <div>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

/** Large counts the way people say them: 1,000,000 reads as 100 万. */
export function readableCount(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  const trim = (n: number) => String(Math.round(n * 10) / 10);
  if (number >= 1e8) return `${trim(number / 1e8)} 亿`;
  if (number >= 1e4) return `${trim(number / 1e4)} 万`;
  return number.toLocaleString();
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  hint,
  onChange,
}: {
  label: string;
  value: string;
  min: number;
  max: number;
  disabled?: boolean;
  hint?: string;
  onChange(value: string): void;
}) {
  return (
    <label className="m-field">
      {label}
      <input
        type="number"
        aria-label={label}
        inputMode="numeric"
        min={min}
        max={max}
        step="1"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? <span className="m-field-hint">{hint}</span> : null}
    </label>
  );
}

const STATE: Record<string, string> = {
  running: "运行中",
  paused: "已暂停",
  needs_you: "需要你处理",
  completed: "已完成",
  starting: "正在启动",
  pending: "待开始",
  working: "执行中",
  review: "复审中",
  rework: "需要返工",
  integrated: "已集成",
};
const TONE: Record<string, "run" | "wait" | "on" | undefined> = {
  running: "run",
  starting: "run",
  working: "run",
  review: "run",
  needs_you: "wait",
  rework: "wait",
  completed: "on",
  integrated: "on",
};
const initialSettings: BuildSettings = {
  tokens: "1000000",
  episodes: "400",
  workers: "2",
  milestones: "20",
  fullAccess: false,
};

export function MobileBuildPanel({
  threadId,
  workspace,
  online,
  chatting,
  visible,
  attachments,
  attachmentsReady,
  onConsumeAttachments,
  onBudget,
  onBusy,
  onOpenAgent,
}: {
  threadId?: string;
  workspace?: string;
  online: boolean;
  chatting: boolean;
  visible: boolean;
  attachments: { path: string; name: string }[];
  attachmentsReady: boolean;
  onConsumeAttachments(): void;
  onBudget(tokens: number): void;
  onBusy(busy: boolean): void;
  onOpenAgent(id: string): void;
}) {
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [readError, setReadError] = useState("");
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(initialSettings);
  const [tokens, setTokens] = useState("");
  const [episodes, setEpisodes] = useState("");
  const [guidance, setGuidance] = useState("");
  const [commit, setCommit] = useState("");
  const [confirmStop, setConfirmStop] = useState(false);
  const budgetForm = useRef<HTMLDivElement>(null);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const serial = useRef(0);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const actionLock = useRef(false);
  const pendingControl = useRef<{
    method: string;
    params: Record<string, unknown>;
  } | null>(null);
  const [uncertain, setUncertain] = useState(false);

  const load = useCallback(
    async (fresh = false) => {
      if (
        !threadId ||
        !online ||
        (!visible && !fresh) ||
        (inFlight.current && !fresh)
      )
        return false;
      const ticket = ++serial.current;
      inFlight.current = true;
      setReading(true);
      try {
        const result = await withProjectReadTimeout(
          window.loom.call<ProjectOverview>("projectRun/overview", {
            threadId,
          }),
        );
        if (
          !result ||
          !Array.isArray(result.agents) ||
          !Array.isArray(result.children) ||
          !Array.isArray(result.events)
        )
          throw new Error("电脑返回的建造信息不完整，请升级电脑端 Loom 后重试");
        if (alive.current && ticket === serial.current) {
          setOverview(result);
          setReadError("");
          return true;
        }
      } catch (cause) {
        if (alive.current && ticket === serial.current)
          setReadError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (ticket === serial.current) {
          inFlight.current = false;
          if (alive.current) setReading(false);
        }
      }
      return false;
    },
    [threadId, online, visible],
  );
  useEffect(() => {
    alive.current = true;
    void load();
    const refresh = () => {
      if (!document.hidden) void load();
    };
    const timer = window.setInterval(refresh, 4000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive.current = false;
      serial.current++;
      inFlight.current = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);
  const proposalId = overview?.proposal?.id;
  useEffect(() => {
    if (overview?.proposal)
      setSettings({
        ...initialSettings,
        tokens: String(overview.proposal.tokenBudget),
        workers: String(overview.proposal.maxWorkers),
      });
  }, [proposalId]); // A poll must not overwrite the person's edits to this proposal.
  const target = overview?.integration || overview?.run;
  useEffect(() => {
    setTokens(String(target?.tokenBudget ?? ""));
    setEpisodes(String(target?.maxEpisodes ?? ""));
  }, [target?.id, target?.tokenBudget, target?.maxEpisodes]);

  const act = async (action: () => Promise<unknown>) => {
    if (actionLock.current || !online) return;
    actionLock.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      await action();
      await load(true);
    } catch (cause) {
      if (alive.current)
        setError(cause instanceof Error ? cause.message : String(cause));
      await load(true);
    } finally {
      actionLock.current = false;
      onBusy(false);
      if (alive.current) setBusy(false);
    }
  };
  const control = (
    action?: "pause" | "resume" | "cancel" | "recover",
    extra: Record<string, unknown> = {},
  ) =>
    act(async () => {
      if (action && !pendingControl.current) {
        if (!overview?.run) throw new Error("请先刷新建造状态");
        pendingControl.current = {
          method: `projectRun/${action}`,
          params: {
            operationId: crypto.randomUUID(),
            runId: overview.run.id,
            ...(action === "recover"
              ? {}
              : { expectedRevision: overview.run.revision }),
            ...extra,
          },
        };
      }
      if (!pendingControl.current) return;
      const pending = pendingControl.current;
      try {
        await window.loom.call(pending.method, pending.params);
        pendingControl.current = null;
        setUncertain(false);
        setConfirmStop(false);
      } catch (cause) {
        // Preserve the operation id for an ambiguous reply, never repeat a mutation under a new id.
        const code = (cause as { code?: string })?.code;
        if (code === "HOST_TIMEOUT" || code === "HOST_OFFLINE")
          setUncertain(true);
        else {
          pendingControl.current = null;
          setUncertain(false);
        }
        throw cause;
      }
    });
  const disabled = busy || !online || Boolean(readError) || uncertain;
  const update = (key: keyof BuildSettings, value: string | boolean) => {
    setSettings((current) => ({ ...current, [key]: value }));
    if (
      key === "tokens" &&
      Number.isInteger(Number(value)) &&
      Number(value) > 0 &&
      Number(value) <= 1e9
    )
      onBudget(Number(value));
  };
  const tokenHint = (value: string) =>
    readableCount(value) ? `约 ${readableCount(value)} token` : "1 – 10 亿";
  const fields = (
    <div className="m-build-fields">
      <NumberField
        label="总 token 预算"
        value={settings.tokens}
        min={1}
        max={1000000000}
        disabled={disabled}
        hint={tokenHint(settings.tokens)}
        onChange={(value) => update("tokens", value)}
      />
      <NumberField
        label="并发 Agent 数"
        value={settings.workers}
        min={1}
        max={8}
        disabled={disabled}
        hint="1 – 8"
        onChange={(value) => update("workers", value)}
      />
      <NumberField
        label="执行轮次上限"
        value={settings.episodes}
        min={1}
        max={10000}
        disabled={disabled}
        hint="1 – 10000"
        onChange={(value) => update("episodes", value)}
      />
      <NumberField
        label="阶段上限"
        value={settings.milestones}
        min={1}
        max={50}
        disabled={disabled}
        hint="1 – 50"
        onChange={(value) => update("milestones", value)}
      />
    </div>
  );
  if (!threadId)
    return (
      <div className="m-build-panel">
        <section className="m-card m-start">
          <span className="m-tile is-accent is-large" aria-hidden="true">
            <Blocks size={24} strokeWidth={1.75} />
          </span>
          <h2>开始建造</h2>
          <p>
            在底部描述目标。电脑会先提出建造方案，你确认预算和并发数后才会启动。
          </p>
          <NumberField
            label="这条建造消息的 token 预算"
            value={settings.tokens}
            min={1}
            max={1000000000}
            hint={tokenHint(settings.tokens)}
            onChange={(value) => update("tokens", value)}
          />
        </section>
        <div className="m-rows">
          <div className="m-row is-static">
            <span className="m-row-icon" aria-hidden="true">
              <GitBranch {...GLYPH} />
            </span>
            <span className="m-row-text">
              <strong>工作目录</strong>
              <small className="m-path">{workspace || "使用电脑默认目录"}</small>
            </span>
          </div>
        </div>
      </div>
    );
  const data = overview
    ? toFlowData(
        overview,
        Date.now(),
        "zh",
        "",
        workspace?.split(/[\\/]/).pop() || "",
      )
    : null;
  const goal = toGoal(overview?.goal);
  const run = overview?.run;
  const proposal = data?.phase === "draft" ? overview?.proposal : null;
  const workspaceLocked = Boolean(data?.phase === "live") || chatting;
  const tokenShare = data?.budget.tokenLimit
    ? Math.min(1, data.budget.tokens / data.budget.tokenLimit)
    : 0;
  const tone = data ? TONE[data.state] : undefined;
  // While a build runs its folder is fixed, so the folder card steps below the live controls.
  const workspaceCard = overview ? (
      <Section title="工作目录与 Git" icon={<GitBranch {...GLYPH} />}>
        <p className="m-path">{overview.workspace?.path || workspace}</p>
        <div className="m-actions">
          <span className="m-badge">{overview.workspace?.branch || "尚无工作分支"}</span>
          <span className={`m-badge${overview.workspace?.changeCount ? " is-wait" : ""}`}>
            {overview.workspace?.changeCount ?? 0} 项未提交改动
          </span>
        </div>
        {workspaceLocked ? (
          <p className="m-note">
            建造期间工作目录保持固定；其他项目请新建建造。
          </p>
        ) : null}
        {overview.workspace?.status === "nested" ? (
          <p className="m-alert">
            请选择 Git 根目录：{overview.workspace.repositoryRoot}
          </p>
        ) : null}
        {overview.workspace?.changes?.length ||
        (!workspaceLocked &&
          ["not_git", "unborn", "detached"].includes(
            overview.workspace?.status || "",
          )) ||
        (!workspaceLocked && Boolean(overview.workspace?.changeCount)) ? (
          <div className="m-disclosures">
            {overview.workspace?.changes?.length ? (
              <Disclosure title="查看工作区改动">
                <ul className="m-build-list">
                  {overview.workspace.changes.map((change) => (
                    <li
                      className="m-path"
                      key={`${change.status}:${change.path}`}
                    >
                      {change.status} · {change.path}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}
            {!workspaceLocked &&
            ["not_git", "unborn", "detached"].includes(
              overview.workspace?.status || "",
            ) ? (
              <Disclosure title="准备建造仓库">
                <p>
                  初始化会创建 Git
                  仓库和空提交，不提交现有文件；创建分支会保留当前文件。
                </p>
                <button
                  type="button"
                  className="m-button is-block"
                  disabled={disabled}
                  onClick={() =>
                    void act(() =>
                      window.loom.call("projectRun/prepareWorkspace", {
                        threadId,
                        confirmed: true,
                        expectedPath: overview.workspace?.path,
                        action:
                          overview.workspace?.status === "detached"
                            ? "branch"
                            : "initialize",
                        branch: `codex/build-${threadId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 12)}`,
                      }),
                    )
                  }
                >
                  确认
                  {overview.workspace?.status === "detached"
                    ? "创建工作分支"
                    : "初始化 Git"}
                </button>
              </Disclosure>
            ) : null}
            {!workspaceLocked && Boolean(overview.workspace?.changeCount) ? (
              <Disclosure title="检查后提交全部工作区改动">
                <p>
                  会提交上面列出的全部改动，作为建造起点。请先确认文件内容。
                </p>
                <label className="m-field">
                  提交说明
                  <input
                    value={commit}
                    maxLength={200}
                    disabled={disabled}
                    onChange={(event) => setCommit(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="m-button is-block"
                  disabled={disabled || !commit.trim()}
                  onClick={() =>
                    void act(async () => {
                      await window.loom.call("projectRun/commitWorkspace", {
                        threadId,
                        message: commit.trim(),
                        expectedCount: overview.workspace?.changeCount,
                      });
                      setCommit("");
                    })
                  }
                >
                  确认提交全部改动
                </button>
              </Disclosure>
            ) : null}
          </div>
        ) : null}
      </Section>
  ) : null;
  return (
    <div className="m-build-panel" aria-label="建造控制面板">
      <div className="m-build-head">
        <span className={`m-badge${tone ? ` is-${tone}` : ""}`}>
          <span
            className={`m-dot is-${tone === "run" ? "run" : tone === "wait" ? "wait" : tone === "on" ? "on" : "idle"}`}
            aria-hidden="true"
          />
          {data ? STATE[data.state] : "正在读取建造状态…"}
        </span>
        <button
          type="button"
          className="m-icon-button"
          aria-label="刷新建造状态"
          disabled={!online || reading}
          onClick={() => void load(true)}
        >
          <RotateCw size={18} strokeWidth={1.75} spinning={reading} />
        </button>
      </div>
      {readError ? (
        <p className="m-alert" role="alert">
          建造状态刷新失败：{readError}。当前显示上次读取的状态。
        </p>
      ) : null}
      {error ? (
        <p className="m-alert" role="alert">
          {error}
        </p>
      ) : null}
      {uncertain ? (
        <Section title="操作结果待确认" tone="wait" icon={<RotateCw {...GLYPH} />}>
          <p>
            连接中断，电脑可能已经执行。先刷新状态，或使用同一操作编号查询重试。
          </p>
          <div className="m-actions">
            <button
              type="button"
              className="m-button is-wait"
              disabled={!online || busy}
              onClick={() => void control()}
            >
              查询并重试上次操作
            </button>
            <button
              type="button"
              className="m-button"
              disabled={!online || reading || busy}
              onClick={() => {
                void load(true).then((success) => {
                  if (success && alive.current) {
                    pendingControl.current = null;
                    setUncertain(false);
                  }
                });
              }}
            >
              刷新后结束重试
            </button>
          </div>
        </Section>
      ) : null}
      {overview ? (
        <>
          {run && overview.agents.some((agent) => agent.approval) ? (
            <Section title="等待你的批准" tone="wait" icon={<ShieldCheck {...GLYPH} />}>
              {overview.agents
                .filter((agent) => agent.approval)
                .map((agent) => (
                  <article className="m-approval-item" key={agent.id}>
                    <p className="m-code">{agent.approval!.command}</p>
                    {agent.approval!.reason ? <p>{agent.approval!.reason}</p> : null}
                    <div className="m-actions">
                      <button
                        type="button"
                        className="m-button"
                        disabled={disabled}
                        onClick={() =>
                          void act(() =>
                            window.loom.call("projectRun/approval", {
                              runId: run.id,
                              sessionId: agent.id,
                              decision: "decline",
                            }),
                          )
                        }
                      >
                        拒绝
                      </button>
                      <button
                        type="button"
                        className="m-button is-wait"
                        disabled={disabled}
                        onClick={() =>
                          void act(() =>
                            window.loom.call("projectRun/approval", {
                              runId: run.id,
                              sessionId: agent.id,
                              decision: "accept",
                            }),
                          )
                        }
                      >
                        仅允许这次
                      </button>
                    </div>
                  </article>
                ))}
            </Section>
          ) : null}
          {goal ? (
            <Section title="长目标" icon={<Compass {...GLYPH} />}>
              <p>{goal.text}</p>
              {goal.detail ? <p className="m-prewrap">{goal.detail}</p> : null}
              {goal.action || goal.dismiss ? (
                <div className="m-actions">
                  {goal.action ? (
                    <button
                      type="button"
                      className="m-button"
                      disabled={disabled}
                      onClick={() =>
                        void act(() =>
                          window.loom.call("projectWeave/goal", {
                            threadId,
                            action: goal.action,
                          }),
                        )
                      }
                    >
                      {goal.action === "pause" ? "暂停目标" : "继续目标"}
                    </button>
                  ) : null}
                  {goal.dismiss ? (
                    <button
                      type="button"
                      className="m-button"
                      disabled={disabled}
                      onClick={() =>
                        void act(() =>
                          window.loom.call("projectWeave/goal", {
                            threadId,
                            action: "dismiss",
                          }),
                        )
                      }
                    >
                      收起目标
                    </button>
                  ) : null}
                </div>
              ) : null}
            </Section>
          ) : null}
          {data?.phase !== "live" ? workspaceCard : null}
          {proposal ? (
            <Section title={proposal.title || "确认建造方案"} icon={<Blocks {...GLYPH} />}>
              <p className="m-prewrap">{proposal.objective}</p>
              {fields}
              <label className="m-check">
                <input
                  type="checkbox"
                  checked={settings.fullAccess}
                  disabled={disabled}
                  onChange={(event) =>
                    update("fullAccess", event.target.checked)
                  }
                />
                完全访问（启动时验证身份）
              </label>
              <p className="m-note">
                默认仅在工作区内执行。预算按总用量计算；已经发出的模型请求仍会完成并计费。
                {attachments.length
                  ? `将包含 ${attachments.length} 个待发送附件。`
                  : ""}
              </p>
              <div className="m-actions">
                <button
                  type="button"
                  className="m-button"
                  disabled={disabled}
                  onClick={() =>
                    void act(() =>
                      window.loom.call("projectWeave/dismiss", {
                        threadId,
                        proposalId: proposal.id,
                      }),
                    )
                  }
                >
                  暂不采用方案
                </button>
                <button
                  type="button"
                  className="m-button is-primary"
                  disabled={
                    disabled ||
                    chatting ||
                    !attachmentsReady ||
                    Boolean(overview.workspace?.changeCount) ||
                    Boolean(
                      overview.workspace?.status &&
                        overview.workspace.status !== "ready",
                    )
                  }
                  onClick={() =>
                    void act(async () => {
                      const params = buildStartParams(
                        threadId,
                        overview,
                        settings,
                        attachments.map((item) => item.path),
                      );
                      if (
                        settings.fullAccess &&
                        !(await confirmBiometric("以完全访问启动建造"))
                      )
                        return;
                      await window.loom.call("projectWeave/start", params);
                      onConsumeAttachments();
                    })
                  }
                >
                  按此设置开始建造
                </button>
              </div>
            </Section>
          ) : null}
          {!run && !proposal ? (
            <section className="m-card m-start">
              <span className="m-tile is-large" aria-hidden="true">
                <MessageSquareText size={22} strokeWidth={1.75} />
              </span>
              <h2>等待建造方案</h2>
              <p>
                在底部描述目标，让电脑提出方案；方案出现后可设置并发、预算和执行轮次。
              </p>
            </section>
          ) : null}
          {run && data?.phase === "live" ? (
            <>
              <Section title={`阶段 ${data.milestone} · ${STATE[data.state]}`} icon={<Blocks {...GLYPH} />}>
                {data.objective ? <p className="m-prewrap">{data.objective}</p> : null}
                {data.narration.summary ? <p>{data.narration.summary}</p> : null}
                {data.narration.text ? <p>{data.narration.text}</p> : null}
                <dl className="m-build-stats">
                  <div className="is-wide">
                    <dt>
                      整体 token · {readableCount(data.budget.tokens) || 0} / {readableCount(data.budget.tokenLimit)}
                      <span className={`m-meter${tokenShare > 0.85 ? " is-high" : ""}`} aria-hidden="true">
                        <i style={{ transform: `scaleX(${tokenShare})` }} />
                      </span>
                    </dt>
                    <dd>
                      {Math.round(tokenShare * 100)}% <small>已用</small>
                    </dd>
                  </div>
                  <div>
                    <dt>本阶段轮次</dt>
                    <dd>
                      {target?.episodesUsed} <small>/ {target?.maxEpisodes}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>活跃 Agent</dt>
                    <dd>
                      {data.agents.length} <small>/ 并发 {data.workers}</small>
                    </dd>
                  </div>
                  <div className="is-wide">
                    <dt>已验收检查</dt>
                    <dd>{data.knowledge.checks}</dd>
                  </div>
                </dl>
                {data.attention ? (
                  <p className="m-alert">{data.attention.reason}</p>
                ) : null}
                <div className="m-actions">
                  {data.controls.pause ? (
                    <button
                      type="button"
                      className="m-button"
                      disabled={disabled}
                      onClick={() => void control("pause")}
                    >
                      暂停建造
                    </button>
                  ) : null}
                  {data.controls.resume ||
                  data.attention?.action === "resume" ? (
                    <button
                      type="button"
                      className="m-button is-tint"
                      disabled={disabled}
                      onClick={() => void control("resume")}
                    >
                      继续建造
                    </button>
                  ) : null}
                  {data.attention?.action === "recover" ? (
                    <button
                      type="button"
                      className="m-button is-tint"
                      disabled={disabled}
                      onClick={() => void control("recover")}
                    >
                      检查并恢复执行
                    </button>
                  ) : null}
                  {data.attention?.action === "budget" ? (
                    <button
                      type="button"
                      className="m-button is-tint"
                      disabled={disabled}
                      onClick={() => {
                        const next = raisedBudget(run, overview.program);
                        setTokens(String(next.tokenBudget));
                        setEpisodes(String(next.maxEpisodes));
                        setBudgetOpen(true);
                        // Bring the form into view once its room has opened, not while it is still growing.
                        const settled = budgetForm.current?.querySelector(".m-disclosure-body");
                        const reveal = () => budgetForm.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
                        if (settled && !budgetOpen) settled.addEventListener("transitionend", reveal, { once: true });
                        else reveal();
                      }}
                    >
                      填入建议追加预算
                    </button>
                  ) : null}
                  {data.controls.cancel ? (
                    <button
                      type="button"
                      className="m-button is-danger"
                      disabled={disabled}
                      aria-expanded={confirmStop}
                      onClick={() => setConfirmStop(!confirmStop)}
                    >
                      停止建造…
                    </button>
                  ) : null}
                </div>
                {confirmStop ? (
                  <div className="m-confirm-inline">
                    <p>停止后不会自动继续，已产生的文件与提交仍保留。</p>
                    <button
                      type="button"
                      className="m-button is-danger"
                      disabled={disabled}
                      onClick={() => void control("cancel")}
                    >
                      确认停止这个建造
                    </button>
                  </div>
                ) : null}
                {data.controls.cancel ? (
                  <div className="m-disclosures">
                    <Disclosure title="调整当前阶段总预算并继续" anchor={budgetForm} open={budgetOpen} onOpenChange={setBudgetOpen}>
                      <div className="m-build-fields">
                        <NumberField
                          label="当前阶段总 token 上限"
                          value={tokens}
                          min={1}
                          max={1000000000}
                          disabled={disabled}
                          hint={tokenHint(tokens)}
                          onChange={setTokens}
                        />
                        <NumberField
                          label="当前阶段总轮次上限"
                          value={episodes}
                          min={1}
                          max={10000}
                          disabled={disabled}
                          onChange={setEpisodes}
                        />
                      </div>
                      <p className="m-note">
                        填写总上限，不是追加数量。并发数和建造权限在启动时固定；下一个建造可重新设置。
                      </p>
                      <button
                        type="button"
                        className="m-button is-block"
                        disabled={disabled}
                        onClick={() => {
                          try {
                            const extra = resumeBudgetParams(
                              overview,
                              tokens,
                              episodes,
                            );
                            void control("resume", extra);
                          } catch (cause) {
                            setError(
                              cause instanceof Error
                                ? cause.message
                                : String(cause),
                            );
                          }
                        }}
                      >
                        应用预算并继续
                      </button>
                    </Disclosure>
                  </div>
                ) : null}
              </Section>
              <Section title="执行 Agent" icon={<Bot {...GLYPH} />}>
                {!data.agents.length ? (
                  <p className="m-note">当前没有活跃 Agent</p>
                ) : null}
                {data.agents.length ? (
                  <div>
                    {data.agents.map((agent) => (
                      <article className="m-agent-card" key={agent.id}>
                        <div>
                          <h3>
                            {agent.name} ·{" "}
                            {agent.role === "planner"
                              ? "规划"
                              : agent.role === "reviewer"
                                ? "复审"
                                : "执行"}
                          </h3>
                          <p>{agent.activity}</p>
                          <p className="m-note">
                            第 {agent.step} 步 · {agent.elapsed}
                          </p>
                        </div>
                        <button
                          type="button"
                          className="m-icon-button"
                          aria-label={`打开${agent.name}的执行对话`}
                          disabled={!online}
                          onClick={() => onOpenAgent(agent.id)}
                        >
                          <ChevronRight size={20} strokeWidth={1.75} />
                        </button>
                      </article>
                    ))}
                  </div>
                ) : null}
              </Section>
              <Section
                title={`任务与验收 · ${data.tasks.filter((task) => task.state === "integrated").length} / ${data.tasks.length}`}
                icon={<ListChecks {...GLYPH} />}
              >
                <ol className="m-build-list">
                  {data.tasks.map((task) => (
                    <li key={task.id} className="m-task">
                      <Disclosure
                        title={task.title}
                        aside={
                          <span className={`m-badge${TONE[task.state] ? ` is-${TONE[task.state]}` : ""}`}>
                            {STATE[task.state] ?? task.state}
                          </span>
                        }
                      >
                        {task.detail ? <p className="m-prewrap">{task.detail}</p> : null}
                        {run.plan
                          .find((item) => item.id === task.id)
                          ?.acceptance.map((criterion, index) => (
                            <p key={index}>验收：{criterion}</p>
                          ))}
                        {task.agentName ? (
                          <p>执行者：{task.agentName}</p>
                        ) : null}
                      </Disclosure>
                    </li>
                  ))}
                </ol>
                {data.outcome ? (
                  <p>{data.outcome.summary || "建造已结束"}</p>
                ) : null}
              </Section>
              <Section title="追加建造指导" icon={<MessageSquareText {...GLYPH} />}>
                <label className="m-field">
                  之后的 Agent 都会收到
                  <textarea
                    rows={3}
                    value={guidance}
                    onChange={(event) => setGuidance(event.target.value)}
                    disabled={disabled}
                  />
                </label>
                <button
                  type="button"
                  className="m-button is-tint is-block"
                  disabled={
                    disabled || !guidance.trim() || !data.controls.cancel
                  }
                  onClick={() =>
                    void act(async () => {
                      await window.loom.call("projectRun/steer", {
                        runId: run.id,
                        text: guidance.trim(),
                      });
                      setGuidance("");
                    })
                  }
                >
                  发送指导
                </button>
              </Section>
            </>
          ) : null}
          {data?.phase === "live" ? workspaceCard : null}
          <Section title="建造参考资料" icon={<FileText {...GLYPH} />}>
            {overview.program?.references?.length ? (
              <ul className="m-build-list">
                {overview.program.references.map((item, index) => (
                  <li className="m-event" key={`${item.name}:${index}`}>
                    <span>{item.name}</span>
                    <span>{Math.ceil(item.bytes / 1024)} KB</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-note">可在底部添加文件，开始建造时一起传入。</p>
            )}
            {overview.program && data?.controls.cancel ? (
              <button
                type="button"
                className="m-button is-block"
                disabled={disabled || !attachmentsReady || !attachments.length}
                onClick={() =>
                  void act(async () => {
                    await window.loom.call("projectProgram/attach", {
                      programId: overview.program!.id,
                      attachments: attachments.map((item) => item.path),
                    });
                    onConsumeAttachments();
                  })
                }
              >
                将待发送附件加入建造资料
              </button>
            ) : null}
          </Section>
          {overview.program?.milestones.length || overview.events.length ? (
            <section className="m-card">
              <Disclosure title="阶段历史与运行事件">
              {overview.program?.milestones.map((item) => (
                <div className="m-event" key={`m${item.index}`}>
                  <span>
                    阶段 {item.index} · {item.complete ? "完成" : "未完成"}
                  </span>
                  <span>
                    {readableCount(item.tokens) || 0} token · {item.episodes} 轮
                  </span>
                </div>
              ))}
              {overview.events
                .slice(-20)
                .reverse()
                .map((event) => (
                  <div className="m-event" key={event.sequence}>
                    <span>{event.kind}</span>
                    {event.reason ? <span>{event.reason}</span> : null}
                  </div>
                ))}
              </Disclosure>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

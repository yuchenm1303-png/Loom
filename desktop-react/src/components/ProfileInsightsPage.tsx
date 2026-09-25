import {
  ArrowLeft,
  Bot,
  CalendarDays,
  Clock3,
  Flame,
  Gauge,
  MessageSquare,
  RefreshCw,
  Sparkles,
  UserRound,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useI18n } from "../i18n";
import type { LoomAccountSnapshot } from "../types/account";
import "./profile-insights.css";

type ProfileUsageDay = {
  date: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  modelCalls: number;
  turns: number;
  toolCalls: number;
  sessions: number;
};

type RankedUsage = {
  name: string;
  calls: number;
  tokens?: number;
};

type ProfileInsightsData = {
  generatedAt: string;
  range: {
    days: number;
    startDate: string;
    endDate: string;
    totalTokens: number;
  };
  totals: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    sessions: number;
    turns: number;
    modelCalls: number;
    toolCalls: number;
    subAgentRuns: number;
    activeDays: number;
  };
  streaks: {
    current: number;
    longest: number;
  };
  peakDay: { date: string; totalTokens: number } | null;
  longestTurnSeconds: number;
  activeHour: { hour: number; turns: number } | null;
  firstActivityDate: string | null;
  lastActivityDate: string | null;
  models: RankedUsage[];
  tools: RankedUsage[];
  reasoning: RankedUsage[];
  days: ProfileUsageDay[];
};

type HeatmapCell = {
  date: string;
  day: ProfileUsageDay | null;
};

interface ProfileInsightsPageProps {
  account: LoomAccountSnapshot;
  onClose(): void;
}

function parseLocalDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, Math.max(0, month - 1), day || 1);
}

function localDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

function buildWeeks(data: ProfileInsightsData | null): HeatmapCell[][] {
  if (!data?.days.length) return [];
  const byDate = new Map(data.days.map((day) => [day.date, day]));
  const start = parseLocalDate(data.range.startDate);
  start.setDate(start.getDate() - mondayIndex(start));
  const end = parseLocalDate(data.range.endDate);
  const weeks: HeatmapCell[][] = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    const week: HeatmapCell[] = [];
    for (let row = 0; row < 7; row += 1) {
      const key = localDateKey(cursor);
      week.push({ date: key, day: byDate.get(key) ?? null });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }
  return weeks;
}

function formatDuration(seconds: number, zh: boolean): string {
  const safe = Math.max(0, Math.round(seconds || 0));
  if (safe < 60) return zh ? `${safe} 秒` : `${safe}s`;
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  if (minutes < 60) return zh ? `${minutes}分 ${rest}秒` : `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return zh ? `${hours}小时 ${mins}分` : `${hours}h ${mins}m`;
}

function activeHourLabel(hour: number | undefined, zh: boolean): string {
  if (hour === undefined) return "—";
  const date = new Date(2026, 0, 1, hour);
  if (!zh) return new Intl.DateTimeFormat("en", { hour: "numeric" }).format(date);
  if (hour < 5) return `凌晨 ${hour} 点`;
  if (hour < 8) return `早上 ${hour} 点`;
  if (hour < 12) return `上午 ${hour} 点`;
  if (hour < 14) return `中午 ${hour} 点`;
  if (hour < 18) return `下午 ${hour - 12} 点`;
  return `晚上 ${hour - 12 || 12} 点`;
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "L";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] ?? ""}${parts.at(-1)?.[0] ?? ""}`.toUpperCase();
}

function MetricCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <article className="profile-metric-card">
      <div className="profile-metric-heading">
        <span className="profile-metric-icon" aria-hidden="true">{icon}</span>
        <span>{label}</span>
      </div>
      <strong>{value}</strong>
      <small>{hint || "\u00a0"}</small>
    </article>
  );
}

function RankedList({
  rows,
  empty,
  total,
  tokenWeighted = false,
}: {
  rows: RankedUsage[];
  empty: string;
  total: number;
  tokenWeighted?: boolean;
}) {
  if (!rows.length) return <div className="profile-empty-row">{empty}</div>;
  return (
    <div className="profile-ranked-list">
      {rows.slice(0, 4).map((row) => {
        const amount = tokenWeighted ? Number(row.tokens || 0) : Number(row.calls || 0);
        const percent = total > 0 ? Math.max(4, (amount / total) * 100) : 0;
        return (
          <div className="profile-ranked-row" key={row.name}>
            <div className="profile-ranked-copy">
              <span title={row.name}>{row.name}</span>
              <em>{tokenWeighted ? `${row.calls}×` : String(row.calls)}</em>
            </div>
            <div className="profile-ranked-track" aria-hidden="true">
              <span style={{ width: `${Math.min(100, percent)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ProfileInsightsPage({ account, onClose }: ProfileInsightsPageProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const locale = zh ? "zh-CN" : "en";
  const [data, setData] = useState<ProfileInsightsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    window.loom.call<ProfileInsightsData>("profile/insights", { days: 371 })
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const identity = account.user?.display_name?.trim()
    || account.user?.email?.split("@")[0]
    || (zh ? "本地用户" : "Local user");
  const secondaryIdentity = account.authenticated && account.user?.email
    ? account.user.email
    : (zh ? "Loom 本地使用档案" : "Local Loom profile");
  const initials = initialsFor(identity);

  const compact = useMemo(
    () => new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }),
    [locale],
  );
  const integer = useMemo(
    () => new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }),
    [locale],
  );
  const dateFormat = useMemo(
    () => new Intl.DateTimeFormat(locale, zh
      ? { month: "short", day: "numeric" }
      : { month: "short", day: "numeric", year: "numeric" }),
    [locale, zh],
  );
  const monthFormat = useMemo(
    () => new Intl.DateTimeFormat(locale, { month: "short" }),
    [locale],
  );

  const weeks = useMemo(() => buildWeeks(data), [data]);
  const maxDayTokens = useMemo(
    () => Math.max(0, ...(data?.days.map((day) => Number(day.totalTokens || 0)) ?? [])),
    [data],
  );
  const heatLevel = (value: number): number => {
    if (value <= 0 || maxDayTokens <= 0) return 0;
    const ratio = Math.log1p(value) / Math.log1p(maxDayTokens);
    return Math.min(4, Math.max(1, Math.ceil(ratio * 4)));
  };
  const monthLabels = useMemo(() => weeks.map((week, index) => {
    const valid = week.filter((cell) => cell.day);
    if (!valid.length) return "";
    const monthStart = valid.find((cell) => parseLocalDate(cell.date).getDate() <= 7);
    const cell = index === 0 ? valid[0] : monthStart;
    return cell ? monthFormat.format(parseLocalDate(cell.date)) : "";
  }), [monthFormat, weeks]);

  const peakDate = data?.peakDay?.date
    ? dateFormat.format(parseLocalDate(data.peakDay.date))
    : undefined;
  const rangeLabel = data
    ? `${dateFormat.format(parseLocalDate(data.range.startDate))} — ${dateFormat.format(parseLocalDate(data.range.endDate))}`
    : "";

  return (
    <section className="profile-insights-page" aria-label={zh ? "个人主页与使用洞察" : "Profile and usage insights"}>
      <header className="profile-insights-topbar">
        <button type="button" className="profile-back-button" onClick={onClose}>
          <ArrowLeft size={15} strokeWidth={1.8} />
          <span>{zh ? "返回对话" : "Back to chat"}</span>
        </button>
        <div className="profile-topbar-title">
          <span>{zh ? "个人主页" : "Profile"}</span>
          <small>{zh ? "使用洞察" : "Usage insights"}</small>
        </div>
        <button
          type="button"
          className="profile-refresh-button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
          title={zh ? "刷新统计" : "Refresh insights"}
          aria-label={zh ? "刷新统计" : "Refresh insights"}
        >
          <RefreshCw size={14} strokeWidth={1.8} />
        </button>
      </header>

      <div className="profile-insights-scroll">
        <main className="profile-insights-content">
          <section className="profile-hero">
            <div className="profile-hero-glow" aria-hidden="true" />
            <div className="profile-avatar" aria-hidden="true">
              {initials}
              <span className={account.authenticated ? "online" : ""} />
            </div>
            <div className="profile-identity">
              <div className="profile-eyebrow">{zh ? "LOOM 使用档案" : "LOOM PROFILE"}</div>
              <h1>{identity}</h1>
              <div className="profile-identity-meta">
                <span>{secondaryIdentity}</span>
                <span className="profile-status-pill">
                  {account.authenticated ? (zh ? "Loom 账号" : "Loom account") : (zh ? "本地" : "Local")}
                </span>
              </div>
              <p>{zh ? "你的 AI 使用轨迹，会随着每一次真实模型调用持续更新。" : "Your AI activity trail, updated from durable model usage."}</p>
            </div>
          </section>

          {loading ? (
            <div className="profile-loading-state">
              <RefreshCw size={19} strokeWidth={1.7} />
              <strong>{zh ? "正在整理你的使用轨迹" : "Building your usage profile"}</strong>
              <span>{zh ? "读取本地会话中的真实 Token 与工具调用…" : "Reading durable token and tool activity from local conversations…"}</span>
            </div>
          ) : error ? (
            <div className="profile-error-state">
              <Gauge size={20} strokeWidth={1.7} />
              <strong>{zh ? "暂时无法读取使用统计" : "Usage insights are unavailable"}</strong>
              <span>{error}</span>
              <button type="button" onClick={() => setReloadKey((value) => value + 1)}>
                {zh ? "重新读取" : "Try again"}
              </button>
            </div>
          ) : data ? (
            <>
              <section className="profile-metrics" aria-label={zh ? "核心统计" : "Key metrics"}>
                <MetricCard
                  icon={<Gauge size={15} />}
                  label={zh ? "累计 Token" : "Total tokens"}
                  value={compact.format(data.totals.totalTokens)}
                  hint={zh ? `${compact.format(data.totals.inputTokens)} 输入 · ${compact.format(data.totals.outputTokens)} 输出` : `${compact.format(data.totals.inputTokens)} in · ${compact.format(data.totals.outputTokens)} out`}
                />
                <MetricCard
                  icon={<Flame size={15} />}
                  label={zh ? "单日峰值 Token" : "Peak token day"}
                  value={compact.format(data.peakDay?.totalTokens ?? 0)}
                  hint={peakDate}
                />
                <MetricCard
                  icon={<CalendarDays size={15} />}
                  label={zh ? "活跃天数" : "Active days"}
                  value={integer.format(data.totals.activeDays)}
                  hint={zh ? "有对话或模型活动的日期" : "Days with conversation activity"}
                />
                <MetricCard
                  icon={<Sparkles size={15} />}
                  label={zh ? "当前连续天数" : "Current streak"}
                  value={zh ? `${data.streaks.current} 天` : `${data.streaks.current}d`}
                  hint={zh ? "连续使用 Loom" : "Consecutive Loom days"}
                />
                <MetricCard
                  icon={<Flame size={15} />}
                  label={zh ? "最长连续天数" : "Longest streak"}
                  value={zh ? `${data.streaks.longest} 天` : `${data.streaks.longest}d`}
                  hint={zh ? "历史最长连续记录" : "Longest recorded streak"}
                />
                <MetricCard
                  icon={<MessageSquare size={15} />}
                  label={zh ? "对话总数" : "Conversations"}
                  value={integer.format(data.totals.sessions)}
                  hint={zh ? `${integer.format(data.totals.turns)} 个用户回合` : `${integer.format(data.totals.turns)} user turns`}
                />
              </section>

              <section className="profile-heatmap-card">
                <div className="profile-section-heading profile-heatmap-heading">
                  <div>
                    <span className="profile-section-icon"><CalendarDays size={16} /></span>
                    <div>
                      <h2>{zh ? "Token 活动" : "Token activity"}</h2>
                      <p>{zh ? "过去一年的真实模型用量热力图" : "A year of durable model usage"}</p>
                    </div>
                  </div>
                  <span className="profile-range-pill">{rangeLabel}</span>
                </div>

                <div className="profile-heatmap-scroll">
                  <div className="profile-heatmap-layout">
                    <div className="profile-weekdays" aria-hidden="true">
                      {(zh ? ["一", "二", "三", "四", "五", "六", "日"] : ["M", "T", "W", "T", "F", "S", "S"]).map((label, index) => (
                        <span key={`${label}-${index}`}>{label}</span>
                      ))}
                    </div>
                    <div className="profile-heatmap-main">
                      <div
                        className="profile-heatmap-grid"
                        style={{ gridTemplateColumns: `repeat(${Math.max(1, weeks.length)}, minmax(11px, 1fr))` }}
                      >
                        {weeks.flatMap((week) => week.map((cell) => {
                          const tokens = cell.day?.totalTokens ?? 0;
                          const level = heatLevel(tokens);
                          const title = cell.day
                            ? `${dateFormat.format(parseLocalDate(cell.date))} · ${integer.format(tokens)} Token · ${cell.day.modelCalls} ${zh ? "次模型调用" : "model calls"}`
                            : "";
                          return (
                            <span
                              key={cell.date}
                              className={`profile-heat-cell level-${level} ${cell.day ? "" : "is-outside"}`}
                              title={title}
                              aria-label={title || undefined}
                            />
                          );
                        }))}
                      </div>
                      <div
                        className="profile-heatmap-months"
                        style={{ gridTemplateColumns: `repeat(${Math.max(1, weeks.length)}, minmax(11px, 1fr))` }}
                        aria-hidden="true"
                      >
                        {monthLabels.map((label, index) => (
                          <span key={`${label}-${index}`}>{label}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="profile-heatmap-footer">
                  <div className="profile-heatmap-summary">
                    <Sparkles size={14} />
                    <span>
                      {zh
                        ? `过去一年记录了 ${compact.format(data.range.totalTokens)} Token、${integer.format(data.totals.modelCalls)} 次模型调用。`
                        : `The last year contains ${compact.format(data.range.totalTokens)} tokens across ${integer.format(data.totals.modelCalls)} model calls.`}
                    </span>
                  </div>
                  <div className="profile-heat-legend" aria-label={zh ? "热力强度" : "Heat intensity"}>
                    <span>{zh ? "较少" : "Less"}</span>
                    {[0, 1, 2, 3, 4].map((level) => <i key={level} className={`level-${level}`} />)}
                    <span>{zh ? "较多" : "More"}</span>
                  </div>
                </div>
              </section>

              <section className="profile-detail-grid">
                <article className="profile-detail-card">
                  <div className="profile-section-heading">
                    <div>
                      <span className="profile-section-icon"><Bot size={16} /></span>
                      <div>
                        <h2>{zh ? "模型偏好" : "Model mix"}</h2>
                        <p>{zh ? "按真实 Token 用量排序" : "Ranked by durable token usage"}</p>
                      </div>
                    </div>
                  </div>
                  <RankedList
                    rows={data.models}
                    empty={zh ? "还没有模型用量记录" : "No model usage yet"}
                    total={Math.max(1, data.totals.totalTokens)}
                    tokenWeighted
                  />
                </article>

                <article className="profile-detail-card">
                  <div className="profile-section-heading">
                    <div>
                      <span className="profile-section-icon"><Wrench size={16} /></span>
                      <div>
                        <h2>{zh ? "工具使用" : "Tool usage"}</h2>
                        <p>{zh ? "最常调用的能力" : "Your most-used capabilities"}</p>
                      </div>
                    </div>
                  </div>
                  <RankedList
                    rows={data.tools}
                    empty={zh ? "还没有工具调用记录" : "No tool calls yet"}
                    total={Math.max(1, data.totals.toolCalls)}
                  />
                </article>

                <article className="profile-detail-card profile-pattern-card">
                  <div className="profile-section-heading">
                    <div>
                      <span className="profile-section-icon"><UserRound size={16} /></span>
                      <div>
                        <h2>{zh ? "活动洞察" : "Activity signals"}</h2>
                        <p>{zh ? "从本地会话聚合出的使用节奏" : "Patterns aggregated from local conversations"}</p>
                      </div>
                    </div>
                  </div>
                  <div className="profile-pattern-list">
                    <div>
                      <span><Clock3 size={14} />{zh ? "最活跃时段" : "Most active hour"}</span>
                      <strong>{activeHourLabel(data.activeHour?.hour, zh)}</strong>
                    </div>
                    <div>
                      <span><Clock3 size={14} />{zh ? "最长单回合" : "Longest turn"}</span>
                      <strong>{formatDuration(data.longestTurnSeconds, zh)}</strong>
                    </div>
                    <div>
                      <span><Bot size={14} />{zh ? "模型调用" : "Model calls"}</span>
                      <strong>{integer.format(data.totals.modelCalls)}</strong>
                    </div>
                    <div>
                      <span><Wrench size={14} />{zh ? "工具调用" : "Tool calls"}</span>
                      <strong>{integer.format(data.totals.toolCalls)}</strong>
                    </div>
                    <div>
                      <span><Sparkles size={14} />{zh ? "子代理派出" : "Sub-agent runs"}</span>
                      <strong>{integer.format(data.totals.subAgentRuns)}</strong>
                    </div>
                  </div>
                </article>
              </section>
            </>
          ) : null}
        </main>
      </div>
    </section>
  );
}

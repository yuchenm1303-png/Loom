import {
  ArrowLeft,
  Bot,
  CalendarCheck,
  CircleHelp,
  Flame,
  Gauge,
  MessagesSquare,
  Network,
  RefreshCw,
  Timer,
  TrendingUp,
  UserRound,
  Wrench,
  Zap,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useI18n } from "../i18n";
import type { LoomAccountSnapshot } from "../types/account";
import {
  buildWeeks,
  daysBetween,
  formatCompact,
  formatDuration,
  heatScale,
  hourLabel,
  monthMarkers,
  parseLocalDate,
  relativeDayLabel,
  toolLabel,
  type HeatCell,
  type HeatLevel,
} from "./profileInsightsModel";
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
    activeToday?: boolean;
  };
  peakDay: { date: string; totalTokens: number } | null;
  longestTurnSeconds: number;
  longestTurnDate?: string | null;
  activeHour: { hour: number; turns: number } | null;
  hours?: number[];
  firstActivityDate: string | null;
  lastActivityDate: string | null;
  models: RankedUsage[];
  modelKinds?: number;
  unrecordedModel?: { calls: number; tokens: number };
  tools: RankedUsage[];
  toolKinds?: number;
  days: ProfileUsageDay[];
};

interface ProfileInsightsPageProps {
  account: LoomAccountSnapshot;
  onClose(): void;
}

type Formatters = {
  zh: boolean;
  compact(value: number): string;
  integer(value: number): string;
  shortDate(value: string): string;
  fullDate(value: string): string;
  longDate(value: string): string;
};

function useFormatters(zh: boolean): Formatters {
  return useMemo(() => {
    const locale = zh ? "zh-CN" : "en-US";
    const integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const short = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" });
    const full = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", day: "numeric" });
    const long = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", day: "numeric", weekday: "short" });
    return {
      zh,
      compact: (value) => formatCompact(value, locale),
      integer: (value) => integer.format(Math.max(0, Math.round(value || 0))),
      shortDate: (value) => short.format(parseLocalDate(value)),
      fullDate: (value) => full.format(parseLocalDate(value)),
      longDate: (value) => long.format(parseLocalDate(value)),
    };
  }, [zh]);
}

/** One initial for CJK names, two for Latin ones ("Ada Lovelace" -> "AL"). */
function initialsFor(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "L";
  if (/[㐀-鿿豈-﫿]/.test(trimmed[0])) return trimmed[0];
  const parts = trimmed.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] ?? ""}${parts.at(-1)?.[0] ?? ""}`.toUpperCase();
}

function share(part: number, whole: number): string {
  if (whole <= 0 || part <= 0) return "0%";
  const percent = (part / whole) * 100;
  return percent < 1 ? "<1%" : `${Math.round(percent)}%`;
}

function ProfileHero({ account, data, format }: { account: LoomAccountSnapshot; data: ProfileInsightsData | null; format: Formatters }) {
  const { zh } = format;
  const displayName = account.user?.display_name?.trim() || account.user?.email?.split("@")[0] || "";
  const identity = displayName || (zh ? "本地用户" : "Local user");
  const today = data?.range.endDate;
  const last = data?.lastActivityDate;
  const lastLabel = last && today
    ? relativeDayLabel(last, today, zh) ?? format.shortDate(last)
    : null;

  return (
    <section className="profile-hero">
      <div className={`profile-avatar ${displayName ? "has-initials" : ""}`} aria-hidden="true">
        {displayName ? initialsFor(displayName) : <UserRound size={30} strokeWidth={1.6} />}
      </div>
      <div className="profile-identity">
        <h1>{identity}</h1>
        <div className="profile-identity-meta">
          {account.authenticated && account.user?.email ? (
            <>
              <span className="profile-identity-email">{account.user.email}</span>
              <span className="profile-status-pill is-account">{zh ? "Loom 账号" : "Loom account"}</span>
            </>
          ) : (
            <span>{zh ? "本地档案 · 数据只保存在这台电脑上" : "Local profile · stored on this computer only"}</span>
          )}
        </div>
      </div>
      {data?.firstActivityDate ? (
        <dl className="profile-tenure">
          <div>
            <dt>{zh ? "开始使用" : "Started"}</dt>
            <dd>{format.fullDate(data.firstActivityDate)}</dd>
          </div>
          {lastLabel ? (
            <div>
              <dt>{zh ? "最近活跃" : "Last active"}</dt>
              <dd>{lastLabel}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}

function MetricCard({ icon, label, value, unit, hint }: { icon: ReactNode; label: string; value: string; unit?: string; hint?: string }) {
  return (
    <article className="profile-metric-card">
      <div className="profile-metric-heading">
        <span className="profile-metric-icon" aria-hidden="true">{icon}</span>
        <span>{label}</span>
      </div>
      <strong>
        {value}
        {unit ? <span className="profile-metric-unit">{unit}</span> : null}
      </strong>
      <small title={hint}>{hint || " "}</small>
    </article>
  );
}

function Metrics({ data, format }: { data: ProfileInsightsData; format: Formatters }) {
  const { zh, compact, integer } = format;
  const { totals, streaks } = data;
  const tenure = data.firstActivityDate ? daysBetween(data.firstActivityDate, data.range.endDate) + 1 : 0;
  const streakHint = [
    streaks.current > 0 && streaks.activeToday === false ? (zh ? "今天还没开始" : "Not yet today") : "",
    zh ? `最长 ${streaks.longest} 天` : `Best ${streaks.longest}d`,
  ].filter(Boolean).join(" · ");

  return (
    <section className="profile-metrics" aria-label={zh ? "核心统计" : "Key metrics"}>
      <MetricCard
        icon={<Gauge size={14} />}
        label={zh ? "累计 Token" : "Total tokens"}
        value={compact(totals.totalTokens)}
        hint={zh ? `输入 ${compact(totals.inputTokens)} · 输出 ${compact(totals.outputTokens)}` : `${compact(totals.inputTokens)} in · ${compact(totals.outputTokens)} out`}
      />
      <MetricCard
        icon={<TrendingUp size={14} />}
        label={zh ? "单日峰值" : "Busiest day"}
        value={compact(data.peakDay?.totalTokens ?? 0)}
        hint={data.peakDay ? format.shortDate(data.peakDay.date) : (zh ? "暂无记录" : "No usage yet")}
      />
      <MetricCard
        icon={<CalendarCheck size={14} />}
        label={zh ? "活跃天数" : "Active days"}
        value={integer(totals.activeDays)}
        unit={zh ? "天" : undefined}
        hint={tenure > 0 ? (zh ? `开始使用后的 ${tenure} 天里` : `of ${tenure} days since you started`) : undefined}
      />
      <MetricCard
        icon={<Flame size={14} />}
        label={zh ? "连续使用" : "Streak"}
        value={integer(streaks.current)}
        unit={zh ? "天" : streaks.current === 1 ? "day" : "days"}
        hint={streakHint}
      />
      <MetricCard
        icon={<MessagesSquare size={14} />}
        label={zh ? "对话" : "Conversations"}
        value={integer(totals.sessions)}
        hint={zh ? `${integer(totals.turns)} 个回合` : `${integer(totals.turns)} turns`}
      />
      <MetricCard
        icon={<Zap size={14} />}
        label={zh ? "模型调用" : "Model calls"}
        value={integer(totals.modelCalls)}
        hint={zh ? `${integer(totals.toolCalls)} 次工具调用` : `${integer(totals.toolCalls)} tool calls`}
      />
    </section>
  );
}

type HeatTooltip = { cell: HeatCell<ProfileUsageDay>; level: HeatLevel; x: number; y: number; below: boolean };

const TokenHeatmap = memo(function TokenHeatmap({ data, format }: { data: ProfileInsightsData; format: Formatters }) {
  const { zh, compact, integer } = format;
  const cardRef = useRef<HTMLElement>(null);
  const [tooltip, setTooltip] = useState<HeatTooltip | null>(null);
  const today = data.range.endDate;

  const weeks = useMemo(() => buildWeeks(data.days, data.range.startDate, data.range.endDate), [data]);
  const markers = useMemo(() => monthMarkers(weeks), [weeks]);
  const levelFor = useMemo(() => heatScale(data.days.map((day) => day.totalTokens)), [data]);
  const monthName = useMemo(() => new Intl.DateTimeFormat(zh ? "zh-CN" : "en-US", { month: "short" }), [zh]);
  const activeDays = data.days.filter((day) => day.totalTokens > 0 || day.turns > 0 || day.toolCalls > 0).length;
  const weekdayLabels = zh ? ["一", "", "三", "", "五", "", ""] : ["Mon", "", "Wed", "", "Fri", "", ""];
  const columns = { "--profile-heat-weeks": Math.max(1, weeks.length) } as CSSProperties;

  const showTooltip = (target: EventTarget | null) => {
    const element = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-heat-index]") : null;
    const card = cardRef.current;
    if (!element || !card) return;
    const [column, row] = String(element.dataset.heatIndex).split(":").map(Number);
    const cell = weeks[column]?.[row];
    if (!cell?.day) return;
    const cardRect = card.getBoundingClientRect();
    const cellRect = element.getBoundingClientRect();
    const halfWidth = 118;
    const center = cellRect.left - cardRect.left + cellRect.width / 2;
    const below = cellRect.top - cardRect.top < 96;
    setTooltip({
      cell,
      level: levelFor(cell.day.totalTokens),
      x: Math.max(halfWidth + 8, Math.min(cardRect.width - halfWidth - 8, center)),
      y: below ? cellRect.bottom - cardRect.top + 8 : cellRect.top - cardRect.top - 8,
      below,
    });
  };

  const summary = zh
    ? `近 12 个月共 ${compact(data.range.totalTokens)} Token，${activeDays} 个活跃日`
    : `${compact(data.range.totalTokens)} tokens across ${activeDays} active days in the last 12 months`;

  return (
    <section ref={cardRef} className="profile-heatmap-card">
      <div className="profile-section-heading">
        <div>
          <span className="profile-section-icon"><CalendarCheck size={15} /></span>
          <div>
            <h2>{zh ? "Token 活动" : "Token activity"}</h2>
            <p>{zh ? "近 12 个月，每格代表一天" : "Last 12 months, one square per day"}</p>
          </div>
        </div>
        <div className="profile-heatmap-total">
          <strong>{compact(data.range.totalTokens)}</strong>
          <span>{zh ? `Token · ${activeDays} 个活跃日` : `tokens · ${activeDays} active days`}</span>
        </div>
      </div>

      <div className="profile-heatmap-scroll">
        <div className="profile-heatmap" style={columns} role="img" aria-label={summary}>
          <div className="profile-heatmap-months" aria-hidden="true">
            {markers.map((marker) => (
              <span key={marker.column} style={{ gridColumn: marker.column + 1 }}>
                {marker.isYearStart
                  ? (zh ? `${marker.date.getFullYear()}年` : String(marker.date.getFullYear()))
                  : monthName.format(marker.date)}
              </span>
            ))}
          </div>
          <div className="profile-heatmap-weekdays" aria-hidden="true">
            {weekdayLabels.map((label, index) => <span key={index}>{label}</span>)}
          </div>
          <div
            className="profile-heatmap-grid"
            aria-hidden="true"
            onMouseOver={(event) => showTooltip(event.target)}
            onMouseLeave={() => setTooltip(null)}
          >
            {weeks.flatMap((week, column) => week.map((cell, row) => {
              if (!cell.day) return <span key={cell.date} className="profile-heat-cell is-padding" />;
              const level = levelFor(cell.day.totalTokens);
              return (
                <span
                  key={cell.date}
                  data-heat-index={`${column}:${row}`}
                  className={`profile-heat-cell level-${level}${cell.date === today ? " is-today" : ""}`}
                />
              );
            }))}
          </div>
        </div>
      </div>

      <div className="profile-heatmap-footer">
        <span>
          {`${format.fullDate(data.range.startDate)} — ${zh ? "今天" : "today"}`}
          {data.peakDay ? (
            <>
              <i aria-hidden="true" />
              {zh
                ? `最多的一天 ${format.shortDate(data.peakDay.date)} · ${compact(data.peakDay.totalTokens)} Token`
                : `Busiest ${format.shortDate(data.peakDay.date)} · ${compact(data.peakDay.totalTokens)} tokens`}
            </>
          ) : null}
        </span>
        <span className="profile-heat-legend" aria-hidden="true">
          <em>{zh ? "少" : "Less"}</em>
          {[0, 1, 2, 3, 4].map((level) => <i key={level} className={`level-${level}`} />)}
          <em>{zh ? "多" : "More"}</em>
        </span>
      </div>

      {tooltip?.cell.day ? (
        <div
          className={`profile-heat-tooltip ${tooltip.below ? "is-below" : ""}`}
          style={{ left: tooltip.x, top: tooltip.y }}
          role="presentation"
        >
          <div className="profile-heat-tooltip-date">
            <i className={`level-${tooltip.level}`} />
            {format.longDate(tooltip.cell.date)}
            {tooltip.cell.date === today ? <em>{zh ? "今天" : "Today"}</em> : null}
          </div>
          {tooltip.cell.day.totalTokens > 0 || tooltip.cell.day.turns > 0 ? (
            <>
              <strong>
                {integer(tooltip.cell.day.totalTokens)}
                <span> Token</span>
              </strong>
              <div className="profile-heat-tooltip-meta">
                {zh
                  ? `${integer(tooltip.cell.day.modelCalls)} 次调用 · ${integer(tooltip.cell.day.turns)} 个回合 · ${integer(tooltip.cell.day.sessions)} 个对话`
                  : `${integer(tooltip.cell.day.modelCalls)} calls · ${integer(tooltip.cell.day.turns)} turns · ${integer(tooltip.cell.day.sessions)} chats`}
              </div>
            </>
          ) : (
            <div className="profile-heat-tooltip-meta">{zh ? "这一天没有使用" : "No activity"}</div>
          )}
        </div>
      ) : null}
    </section>
  );
});

function RankedRow({ label, detail, value, meta, ratio, title, muted = false }: {
  label: ReactNode;
  detail?: string;
  value: string;
  meta?: string;
  ratio: number;
  title?: string;
  muted?: boolean;
}) {
  return (
    <div className={`profile-ranked-row ${muted ? "is-muted" : ""}`} title={title}>
      <div className="profile-ranked-copy">
        <span className="profile-ranked-label">
          {label}
          {detail ? <code>{detail}</code> : null}
        </span>
        <span className="profile-ranked-value">
          <strong>{value}</strong>
          {meta ? <em>{meta}</em> : null}
        </span>
      </div>
      <div className="profile-ranked-track" aria-hidden="true">
        <span style={{ "--profile-bar": Math.max(0.02, Math.min(1, ratio)) } as CSSProperties} />
      </div>
    </div>
  );
}

function DetailCard({ icon, title, subtitle, footer, className = "", children }: {
  icon: ReactNode;
  title: string;
  subtitle: string;
  footer?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <article className={`profile-detail-card ${className}`}>
      <div className="profile-section-heading">
        <div>
          <span className="profile-section-icon">{icon}</span>
          <div>
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
        </div>
      </div>
      <div className="profile-detail-body">{children}</div>
      {footer ? <div className="profile-detail-footer">{footer}</div> : null}
    </article>
  );
}

function ModelMix({ data, format }: { data: ProfileInsightsData; format: Formatters }) {
  const { zh, compact, integer } = format;
  const rows = data.models.slice(0, 5);
  const unrecorded = data.unrecordedModel;
  const top = Math.max(1, Number(rows[0]?.tokens || 0), Number(unrecorded?.tokens || 0));
  const total = Math.max(1, data.totals.totalTokens);
  const kinds = data.modelKinds ?? data.models.length;

  return (
    <DetailCard
      icon={<Bot size={15} />}
      title={zh ? "模型偏好" : "Models"}
      subtitle={zh ? "按 Token 用量排序" : "Ranked by tokens"}
      footer={kinds > 0 ? (zh ? `共用过 ${kinds} 个模型` : `${kinds} models used`) : undefined}
    >
      {rows.length || unrecorded?.calls ? (
        <div className="profile-ranked-list">
          {rows.map((row) => (
            <RankedRow
              key={row.name}
              label={row.name}
              value={compact(Number(row.tokens || 0))}
              meta={share(Number(row.tokens || 0), total)}
              ratio={Number(row.tokens || 0) / top}
              title={zh
                ? `${row.name}：${integer(Number(row.tokens || 0))} Token，${integer(row.calls)} 次调用`
                : `${row.name}: ${integer(Number(row.tokens || 0))} tokens, ${integer(row.calls)} calls`}
            />
          ))}
          {unrecorded?.calls ? (
            <RankedRow
              muted
              label={(
                <>
                  {zh ? "未记录模型" : "Unrecorded model"}
                  <CircleHelp size={12} aria-hidden="true" />
                </>
              )}
              value={compact(unrecorded.tokens)}
              meta={share(unrecorded.tokens, total)}
              ratio={unrecorded.tokens / top}
              title={zh
                ? `较早的对话没有保存模型名称：${integer(unrecorded.calls)} 次调用，${integer(unrecorded.tokens)} Token`
                : `Older conversations did not save a model name: ${integer(unrecorded.calls)} calls, ${integer(unrecorded.tokens)} tokens`}
            />
          ) : null}
        </div>
      ) : (
        <div className="profile-empty-row">{zh ? "还没有模型用量记录" : "No model usage yet"}</div>
      )}
    </DetailCard>
  );
}

function ToolUsage({ data, format }: { data: ProfileInsightsData; format: Formatters }) {
  const { zh, integer } = format;
  const rows = data.tools.slice(0, 5);
  const top = Math.max(1, Number(rows[0]?.calls || 0));
  const kinds = data.toolKinds ?? data.tools.length;

  return (
    <DetailCard
      icon={<Wrench size={15} />}
      title={zh ? "工具使用" : "Tools"}
      subtitle={zh ? "Loom 最常替你做的事" : "What Loom does for you most"}
      footer={kinds > 0
        ? (zh ? `共 ${kinds} 种工具 · ${integer(data.totals.toolCalls)} 次调用` : `${kinds} tools · ${integer(data.totals.toolCalls)} calls`)
        : undefined}
    >
      {rows.length ? (
        <div className="profile-ranked-list">
          {rows.map((row) => {
            const label = toolLabel(row.name, zh);
            return (
              <RankedRow
                key={row.name}
                label={label ?? row.name}
                detail={label ? row.name : undefined}
                value={integer(row.calls)}
                meta={zh ? "次" : undefined}
                ratio={row.calls / top}
              />
            );
          })}
        </div>
      ) : (
        <div className="profile-empty-row">{zh ? "还没有工具调用记录" : "No tool calls yet"}</div>
      )}
    </DetailCard>
  );
}

function ActivityRhythm({ data, format }: { data: ProfileInsightsData; format: Formatters }) {
  const { zh, integer } = format;
  const hours = data.hours?.length === 24 ? data.hours : null;
  const peak = data.activeHour?.hour;
  const busiest = Math.max(1, ...(hours ?? [0]));
  const perConversation = data.totals.sessions > 0 ? data.totals.turns / data.totals.sessions : 0;

  return (
    <DetailCard
      className="profile-rhythm-card"
      icon={<Timer size={15} />}
      title={zh ? "使用节奏" : "Rhythm"}
      subtitle={zh ? "一天中什么时候最常用 Loom" : "When in the day you use Loom"}
    >
      {hours ? (
        <div className="profile-hours">
          <div className="profile-hours-headline">
            <span>{zh ? "最活跃" : "Most active"}</span>
            <strong>{peak === undefined ? "—" : hourLabel(peak, zh)}</strong>
          </div>
          <div className="profile-hours-bars" role="img" aria-label={peak === undefined ? undefined : `${zh ? "最活跃时段" : "Most active hour"}: ${hourLabel(peak, zh)}`}>
            {hours.map((count, hour) => (
              <span
                key={hour}
                className={`${count ? "" : "is-empty"} ${hour === peak ? "is-peak" : ""}`}
                style={{ "--profile-hour": count / busiest } as CSSProperties}
                title={zh ? `${hour}:00 · ${integer(count)} 个回合` : `${hour}:00 · ${integer(count)} turns`}
              />
            ))}
          </div>
          <div className="profile-hours-axis" aria-hidden="true">
            <span>0</span><span>6</span><span>12</span><span>18</span><span>24</span>
          </div>
        </div>
      ) : null}
      <div className="profile-pattern-list">
        <div>
          <span><Timer size={13} />{zh ? "最长单回合" : "Longest turn"}</span>
          <strong>
            {formatDuration(data.longestTurnSeconds, zh)}
            {data.longestTurnDate ? <em>{format.shortDate(data.longestTurnDate)}</em> : null}
          </strong>
        </div>
        <div>
          <span><MessagesSquare size={13} />{zh ? "每个对话平均" : "Per conversation"}</span>
          <strong>{zh ? `${perConversation.toFixed(1)} 个回合` : `${perConversation.toFixed(1)} turns`}</strong>
        </div>
        <div>
          <span><Network size={13} />{zh ? "派出子代理" : "Sub-agents spawned"}</span>
          <strong>{zh ? `${integer(data.totals.subAgentRuns)} 次` : integer(data.totals.subAgentRuns)}</strong>
        </div>
      </div>
    </DetailCard>
  );
}

function ProfileSkeleton() {
  return (
    <div className="profile-skeleton" aria-hidden="true">
      <div className="profile-metrics">
        {Array.from({ length: 6 }, (_, index) => <div key={index} className="profile-metric-card is-skeleton"><i /><b /><i /></div>)}
      </div>
      <div className="profile-heatmap-card is-skeleton"><i /><b /></div>
      <div className="profile-detail-grid">
        {Array.from({ length: 3 }, (_, index) => <div key={index} className="profile-detail-card is-skeleton"><i /><b /><b /><b /></div>)}
      </div>
    </div>
  );
}

export function ProfileInsightsPage({ account, onClose }: ProfileInsightsPageProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const format = useFormatters(zh);
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

  const updatedAt = useMemo(() => {
    if (!data?.generatedAt) return "";
    const stamp = new Date(data.generatedAt);
    if (Number.isNaN(stamp.getTime())) return "";
    return new Intl.DateTimeFormat(zh ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" }).format(stamp);
  }, [data, zh]);

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
        <div className="profile-topbar-actions">
          {updatedAt ? (
            <span className="profile-updated-at">
              {loading ? (zh ? "正在更新…" : "Updating…") : (zh ? `更新于 ${updatedAt}` : `Updated ${updatedAt}`)}
            </span>
          ) : null}
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
        </div>
      </header>

      <div className="profile-insights-scroll">
        <main className="profile-insights-content" aria-busy={loading || undefined}>
          <ProfileHero account={account} data={data} format={format} />

          {error ? (
            <div className="profile-error-state" role="alert">
              <Gauge size={20} strokeWidth={1.7} />
              <strong>{zh ? "暂时无法读取使用统计" : "Usage insights are unavailable"}</strong>
              <span>{error}</span>
              <button type="button" onClick={() => setReloadKey((value) => value + 1)}>
                {zh ? "重新读取" : "Try again"}
              </button>
            </div>
          ) : data ? (
            <div className={`profile-insights-body ${loading ? "is-refreshing" : ""}`}>
              <Metrics data={data} format={format} />
              <TokenHeatmap data={data} format={format} />
              <section className="profile-detail-grid">
                <ModelMix data={data} format={format} />
                <ToolUsage data={data} format={format} />
                <ActivityRhythm data={data} format={format} />
              </section>
            </div>
          ) : loading ? (
            <ProfileSkeleton />
          ) : null}
        </main>
      </div>
    </section>
  );
}

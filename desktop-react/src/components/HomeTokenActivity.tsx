import { Activity, ArrowRight } from "./icons";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { useI18n } from "../i18n";
import {
  buildWeeks,
  formatCompact,
  heatScale,
  monthMarkers,
  parseLocalDate,
  type HeatLevel,
} from "./profileInsightsModel";
import "./home-token-activity.css";

type UsageDay = {
  date: string;
  totalTokens: number;
  modelCalls: number;
};

type HomeUsageInsights = {
  range: {
    startDate: string;
    endDate: string;
    totalTokens: number;
  };
  totals: {
    activeDays: number;
    modelCalls: number;
  };
  streaks: {
    current: number;
  };
  peakDay: {
    date: string;
    totalTokens: number;
  } | null;
  days: UsageDay[];
};

type HeatTooltip = {
  date: string;
  tokens: number;
  modelCalls: number;
  level: HeatLevel;
  /** Centre of the hovered cell, relative to the card. */
  x: number;
  y: number;
};

const LEVELS: readonly HeatLevel[] = [0, 1, 2, 3, 4];
const PLACEHOLDER_WEEKS = 53;
const TOOLTIP_INSET = 8;
// Month labels start past the left-edge fade (about three columns wide).
const FIRST_LABEL_COLUMN = 3;

let cachedInsights: HomeUsageInsights | null = null;
let inflightInsights: Promise<HomeUsageInsights> | null = null;

function loadInsights(): Promise<HomeUsageInsights> {
  if (cachedInsights) return Promise.resolve(cachedInsights);
  if (inflightInsights) return inflightInsights;
  // The home screen mounts before the app server has been spawned, so go
  // through connect() first -- it is idempotent and starts the server when it
  // is not running yet. A bare call() here rejects on cold start, which flips
  // `failed` and hides the whole panel for the rest of the session.
  inflightInsights = window.loom.connect()
    .then(() => window.loom.call<HomeUsageInsights>("profile/insights", { days: 371 }))
    .then((result) => {
      cachedInsights = result;
      return result;
    })
    .finally(() => {
      inflightInsights = null;
    });
  return inflightInsights;
}

export function HomeTokenActivity({ onOpenInsights }: { onOpenInsights?(): void }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const locale = zh ? "zh-CN" : "en-US";
  const rootRef = useRef<HTMLElement>(null);
  const heatmapRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<HomeUsageInsights | null>(cachedInsights);
  const [failed, setFailed] = useState(false);
  const [tooltip, setTooltip] = useState<HeatTooltip | null>(null);
  const [capacity, setCapacity] = useState(PLACEHOLDER_WEEKS);

  // How many whole weeks fit beside the summary. Only those are rendered, so
  // the grid never cuts a column or a month label in half.
  useLayoutEffect(() => {
    const element = heatmapRef.current;
    if (!element) return;
    const measure = () => {
      const style = getComputedStyle(element);
      const cell = parseFloat(style.getPropertyValue("--heat-cell")) || 11;
      const gap = parseFloat(style.getPropertyValue("--heat-gap")) || 3;
      const next = Math.max(1, Math.floor((element.clientWidth + gap) / (cell + gap)));
      setCapacity((current) => (current === next ? current : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (cachedInsights) return;
    let cancelled = false;
    loadInsights()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const weeks = useMemo(
    () => (data ? buildWeeks(data.days, data.range.startDate, data.range.endDate).slice(-capacity) : []),
    [capacity, data],
  );
  const cells = useMemo(() => weeks.flat(), [weeks]);
  // A label needs about three columns: skip one that would sit in the fade
  // or run past the newest week.
  const markers = useMemo(
    () => monthMarkers(weeks).filter((marker) => (
      marker.column >= FIRST_LABEL_COLUMN && marker.column < weeks.length - 2
    )),
    [weeks],
  );
  // Same scale as the profile page, so a day reads the same in both places.
  const levelFor = useMemo(
    () => heatScale(data?.days.map((day) => Number(day.totalTokens || 0)) ?? []),
    [data],
  );
  // Keep the tooltip inside the card: centre it on the cell, then shift it by
  // its measured width before paint when the cell is near an edge.
  useLayoutEffect(() => {
    const element = tooltipRef.current;
    const root = rootRef.current;
    if (!tooltip || !element || !root) return;
    const half = element.offsetWidth / 2;
    const max = root.clientWidth - half - TOOLTIP_INSET;
    element.style.left = `${Math.max(half + TOOLTIP_INSET, Math.min(max, tooltip.x))}px`;
  }, [tooltip]);

  const formats = useMemo(() => ({
    month: new Intl.DateTimeFormat(locale, { month: "short" }),
    day: new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }),
    fullDay: new Intl.DateTimeFormat(locale, { weekday: "short", month: "short", day: "numeric", year: "numeric" }),
    exact: new Intl.NumberFormat(locale),
  }), [locale]);

  if (failed) return null;

  const loading = !data;
  const total = data?.range.totalTokens ?? 0;
  const activeDays = data?.totals.activeDays ?? 0;
  const streak = data?.streaks.current ?? 0;
  const peak = data?.peakDay ?? null;
  const columns = weeks.length || Math.min(capacity, PLACEHOLDER_WEEKS);
  const heatmapStyle = { "--home-heat-weeks": columns } as CSSProperties;

  const peakDate = peak ? parseLocalDate(peak.date) : null;
  const peakLabel = peakDate
    ? (peakDate.getFullYear() === new Date().getFullYear() ? formats.day : formats.fullDay).format(peakDate)
    : "";

  const showTooltip = (event: MouseEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>("[data-cell]");
    const cell = target ? cells[Number(target.dataset.cell)] : undefined;
    const root = rootRef.current;
    if (!target || !cell?.day || !root) {
      setTooltip(null);
      return;
    }
    if (tooltip?.date === cell.date) return;
    const rootRect = root.getBoundingClientRect();
    const cellRect = target.getBoundingClientRect();
    setTooltip({
      date: cell.date,
      tokens: cell.day.totalTokens,
      modelCalls: cell.day.modelCalls,
      level: levelFor(cell.day.totalTokens),
      x: cellRect.left - rootRect.left + cellRect.width / 2,
      y: cellRect.top - rootRect.top - 8,
    });
  };

  const summaryLabel = data
    ? zh
      ? `过去一年共 ${formatCompact(total, locale)} Token，活跃 ${activeDays} 天`
      : `${formatCompact(total, locale)} tokens over ${activeDays} active days in the past year`
    : undefined;

  return (
    <section
      ref={rootRef}
      className={`home-token-activity${loading ? " is-loading" : ""}`}
      aria-label={zh ? "Token 用量" : "Token activity"}
      aria-busy={loading || undefined}
    >
      <div className="home-token-summary">
        <div className="home-token-label">
          <Activity size={13} strokeWidth={2} aria-hidden="true" />
          <span>{zh ? "Token 用量" : "Token activity"}</span>
        </div>

        <div className="home-token-total">
          {loading ? <i className="home-token-skeleton is-total" /> : (
            <>
              <strong>{formatCompact(total, locale)}</strong>
              <span>{zh ? "Token · 近一年" : "tokens · past year"}</span>
            </>
          )}
        </div>

        <ul className="home-token-facts">
          {loading ? (
            <>
              <li><i className="home-token-skeleton" /></li>
              <li><i className="home-token-skeleton is-short" /></li>
            </>
          ) : activeDays ? (
            <>
              <li>
                {zh ? <>活跃 <b>{activeDays}</b> 天</> : <><b>{activeDays}</b> active {activeDays === 1 ? "day" : "days"}</>}
                {streak > 1 ? <span className="home-token-streak">{zh ? `连续 ${streak} 天` : `${streak}-day streak`}</span> : null}
              </li>
              {peak ? (
                <li>
                  {zh ? "峰值" : "Peak"} <b>{formatCompact(peak.totalTokens, locale)}</b>
                  <span className="home-token-dot" aria-hidden="true" />
                  {peakLabel}
                </li>
              ) : null}
            </>
          ) : (
            <li className="home-token-empty">
              {zh ? "开始第一个对话后，这里会记录你的用量。" : "Your usage appears here after your first conversation."}
            </li>
          )}
        </ul>

        {onOpenInsights ? (
          <button className="home-token-link" type="button" onClick={onOpenInsights}>
            {zh ? "查看使用洞察" : "View insights"}
            <ArrowRight size={12} strokeWidth={2} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <div className="home-token-chart">
        <div ref={heatmapRef} className="home-token-heatmap" style={heatmapStyle}>
          <div className="home-token-months" aria-hidden="true">
            {markers.map((marker) => (
              <span key={marker.column} style={{ gridColumn: marker.column + 1 }}>
                {marker.isYearStart
                  ? (zh ? `${marker.date.getFullYear()}年` : String(marker.date.getFullYear()))
                  : formats.month.format(marker.date)}
              </span>
            ))}
          </div>

          <div
            className="home-token-grid"
            role="img"
            aria-label={summaryLabel}
            onMouseOver={loading ? undefined : showTooltip}
            onMouseLeave={() => setTooltip(null)}
          >
            {loading
              ? Array.from({ length: columns * 7 }, (_, index) => <span className="home-token-cell" key={index} />)
              : cells.map((cell, index) => (
                <span
                  key={cell.date}
                  className={`home-token-cell${cell.day ? ` level-${levelFor(cell.day.totalTokens)}` : " is-outside"}`}
                  data-cell={cell.day ? index : undefined}
                />
              ))}
          </div>
        </div>

        <div className="home-token-legend" aria-hidden="true">
          <span>{zh ? "少" : "Less"}</span>
          {LEVELS.map((level) => <i className={`level-${level}`} key={level} />)}
          <span>{zh ? "多" : "More"}</span>
        </div>
      </div>

      {tooltip ? (
        <div ref={tooltipRef} className="home-token-tooltip" style={{ left: tooltip.x, top: tooltip.y }} role="presentation">
          <div className="home-token-tooltip-date">
            <i className={`level-${tooltip.level}`} />
            {formats.fullDay.format(parseLocalDate(tooltip.date))}
          </div>
          <div className="home-token-tooltip-value">
            <strong>{formats.exact.format(tooltip.tokens)}</strong>
            <span>{zh ? "Token" : tooltip.tokens === 1 ? "token" : "tokens"}</span>
          </div>
          <div className="home-token-tooltip-meta">
            {zh
              ? `${formats.exact.format(tooltip.modelCalls)} 次模型调用`
              : `${formats.exact.format(tooltip.modelCalls)} model ${tooltip.modelCalls === 1 ? "call" : "calls"}`}
          </div>
        </div>
      ) : null}
    </section>
  );
}

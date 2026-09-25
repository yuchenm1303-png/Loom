import { Activity, Flame, Gauge, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
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

type HeatCell = {
  date: string;
  day: UsageDay | null;
};

type HeatTooltip = {
  date: string;
  tokens: number;
  modelCalls: number;
  x: number;
  y: number;
  level: number;
};

let cachedInsights: HomeUsageInsights | null = null;
let inflightInsights: Promise<HomeUsageInsights> | null = null;

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

function buildWeeks(data: HomeUsageInsights | null): HeatCell[][] {
  if (!data?.days.length) return [];
  const byDate = new Map(data.days.map((day) => [day.date, day]));
  const start = parseLocalDate(data.range.startDate);
  start.setDate(start.getDate() - mondayIndex(start));
  const end = parseLocalDate(data.range.endDate);
  const weeks: HeatCell[][] = [];
  const cursor = new Date(start);

  while (cursor <= end) {
    const week: HeatCell[] = [];
    for (let row = 0; row < 7; row += 1) {
      const key = localDateKey(cursor);
      week.push({ date: key, day: byDate.get(key) ?? null });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }

  return weeks;
}

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

function formatCompact(value: number): string {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Math.max(0, value || 0));
}

function formatExact(value: number): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
  }).format(Math.max(0, value || 0));
}

function formatTooltipDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(parseLocalDate(value));
}

function buildMonthLabels(weeks: HeatCell[][]): string[] {
  let previousMonth = -1;
  return weeks.map((week) => {
    const firstRealCell = week.find((cell) => cell.day);
    if (!firstRealCell) return "";
    const date = parseLocalDate(firstRealCell.date);
    const month = date.getMonth();
    if (month === previousMonth) return "";
    previousMonth = month;
    return new Intl.DateTimeFormat("en-US", { month: "short" }).format(date);
  });
}

export function HomeTokenActivity() {
  const rootRef = useRef<HTMLElement>(null);
  const [data, setData] = useState<HomeUsageInsights | null>(cachedInsights);
  const [failed, setFailed] = useState(false);
  const [tooltip, setTooltip] = useState<HeatTooltip | null>(null);

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

  const weeks = useMemo(() => buildWeeks(data), [data]);
  const monthLabels = useMemo(() => buildMonthLabels(weeks), [weeks]);
  const maxTokens = useMemo(
    () => Math.max(0, ...(data?.days.map((day) => Number(day.totalTokens || 0)) ?? [])),
    [data],
  );

  const levelFor = (tokens: number): number => {
    if (tokens <= 0 || maxTokens <= 0) return 0;
    const ratio = Math.log1p(tokens) / Math.log1p(maxTokens);
    return Math.min(4, Math.max(1, Math.ceil(ratio * 4)));
  };

  const showTooltip = (target: HTMLElement, cell: HeatCell) => {
    if (!cell.day || !rootRef.current) return;
    const rootRect = rootRef.current.getBoundingClientRect();
    const cellRect = target.getBoundingClientRect();
    const tooltipHalfWidth = 112;
    const rawX = cellRect.left - rootRect.left + cellRect.width / 2;
    const x = Math.max(tooltipHalfWidth, Math.min(rootRect.width - tooltipHalfWidth, rawX));
    const y = cellRect.top - rootRect.top - 9;
    setTooltip({
      date: cell.date,
      tokens: cell.day.totalTokens,
      modelCalls: cell.day.modelCalls,
      x,
      y,
      level: levelFor(cell.day.totalTokens),
    });
  };

  if (failed) return null;

  const heatmapStyle = weeks.length
    ? ({ "--home-heat-columns": weeks.length } as CSSProperties)
    : undefined;

  return (
    <section ref={rootRef} className="home-token-activity" aria-label="Token activity">
      <div className="home-token-heading">
        <div className="home-token-title">
          <span className="home-token-mark"><Activity size={13} strokeWidth={1.8} /></span>
          <div>
            <strong>Token activity</strong>
            <span>Last 12 months</span>
          </div>
        </div>

        <div className="home-token-stats" aria-label="Token usage summary">
          <span aria-label="Tokens used in the displayed period">
            <Gauge size={12} />
            <strong>{data ? formatCompact(data.range.totalTokens) : "—"}</strong>
            <em>tokens</em>
          </span>
          <span aria-label="Active days">
            <Sparkles size={12} />
            <strong>{data ? data.totals.activeDays : "—"}</strong>
            <em>days</em>
          </span>
          <span aria-label="Current streak">
            <Flame size={12} />
            <strong>{data ? data.streaks.current : "—"}</strong>
            <em>streak</em>
          </span>
        </div>
      </div>

      <div className="home-token-chart">
        <div className="home-token-month-row" aria-hidden="true">
          <span className="home-token-month-gutter" />
          <div className="home-token-months" style={heatmapStyle}>
            {monthLabels.map((label, index) => (
              <span key={`${label || "blank"}-${index}`}>{label}</span>
            ))}
          </div>
        </div>

        <div className="home-token-heatmap-shell">
          <div className="home-token-weekdays" aria-hidden="true">
            <span>M</span>
            <span />
            <span>W</span>
            <span />
            <span>F</span>
            <span />
            <span />
          </div>

          <div className="home-token-heatmap" style={heatmapStyle}>
            {data && weeks.length ? weeks.flatMap((week) => week.map((cell) => {
              const tokens = cell.day?.totalTokens ?? 0;
              const level = levelFor(tokens);
              return (
                <span
                  key={cell.date}
                  className={`home-token-cell level-${level} ${cell.day ? "" : "outside"}`}
                  aria-label={cell.day
                    ? `${formatTooltipDate(cell.date)}, ${formatExact(tokens)} tokens, ${cell.day.modelCalls} model calls`
                    : undefined}
                  onMouseEnter={(event) => showTooltip(event.currentTarget, cell)}
                  onMouseLeave={() => setTooltip(null)}
                />
              );
            })) : Array.from({ length: 371 }, (_, index) => (
              <span className="home-token-cell loading" key={index} />
            ))}
          </div>
        </div>
      </div>

      <div className="home-token-footer">
        <span>
          {data?.peakDay
            ? `Peak ${formatCompact(data.peakDay.totalTokens)} tokens · ${data.peakDay.date}`
            : "Your usage history will appear here"}
        </span>
        <span className="home-token-legend" aria-hidden="true">
          <em>Less</em>
          {[0, 1, 2, 3, 4].map((level) => <i className={`level-${level}`} key={level} />)}
          <em>More</em>
        </span>
      </div>

      {tooltip ? (
        <div
          className="home-token-tooltip"
          style={{ left: tooltip.x, top: tooltip.y }}
          role="presentation"
        >
          <div className="home-token-tooltip-head">
            <span className={`home-token-tooltip-swatch level-${tooltip.level}`} />
            <strong>{formatTooltipDate(tooltip.date)}</strong>
          </div>
          <div className="home-token-tooltip-value">
            <strong>{formatExact(tooltip.tokens)}</strong>
            <span>tokens</span>
          </div>
          <div className="home-token-tooltip-meta">
            <span>{tooltip.modelCalls} model {tooltip.modelCalls === 1 ? "call" : "calls"}</span>
            <span className="home-token-tooltip-dot" />
            <span>{formatCompact(tooltip.tokens)} total</span>
          </div>
        </div>
      ) : null}
    </section>
  );
}

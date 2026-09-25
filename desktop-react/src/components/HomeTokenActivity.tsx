import { Activity, Flame, Gauge, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Math.max(0, value || 0));
}

export function HomeTokenActivity() {
  const [data, setData] = useState<HomeUsageInsights | null>(cachedInsights);
  const [failed, setFailed] = useState(false);

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
  const maxTokens = useMemo(
    () => Math.max(0, ...(data?.days.map((day) => Number(day.totalTokens || 0)) ?? [])),
    [data],
  );

  const levelFor = (tokens: number): number => {
    if (tokens <= 0 || maxTokens <= 0) return 0;
    const ratio = Math.log1p(tokens) / Math.log1p(maxTokens);
    return Math.min(4, Math.max(1, Math.ceil(ratio * 4)));
  };

  if (failed) return null;

  return (
    <section className="home-token-activity" aria-label="Token activity">
      <div className="home-token-heading">
        <div className="home-token-title">
          <span className="home-token-mark"><Activity size={13} strokeWidth={1.8} /></span>
          <div>
            <strong>Token activity</strong>
            <span>Last 12 months</span>
          </div>
        </div>

        <div className="home-token-stats" aria-label="Token usage summary">
          <span title="Tokens used in the displayed period">
            <Gauge size={12} />
            <strong>{data ? formatCompact(data.range.totalTokens) : "—"}</strong>
            <em>tokens</em>
          </span>
          <span title="Active days">
            <Sparkles size={12} />
            <strong>{data ? data.totals.activeDays : "—"}</strong>
            <em>days</em>
          </span>
          <span title="Current streak">
            <Flame size={12} />
            <strong>{data ? data.streaks.current : "—"}</strong>
            <em>streak</em>
          </span>
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

        <div className="home-token-heatmap">
          {data && weeks.length ? weeks.flatMap((week) => week.map((cell) => {
            const tokens = cell.day?.totalTokens ?? 0;
            const modelCalls = cell.day?.modelCalls ?? 0;
            const title = cell.day
              ? `${cell.date} · ${formatCompact(tokens)} tokens · ${modelCalls} model calls`
              : cell.date;
            return (
              <span
                key={cell.date}
                className={`home-token-cell level-${levelFor(tokens)} ${cell.day ? "" : "outside"}`}
                title={title}
              />
            );
          })) : Array.from({ length: 371 }, (_, index) => (
            <span className="home-token-cell loading" key={index} />
          ))}
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
    </section>
  );
}

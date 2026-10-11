/**
 * The usage card on the home screen, kept between launches.
 *
 * Its numbers come from reading every conversation on disk, which takes seconds
 * and only becomes possible once the local runtime is up. The card used to sit on
 * a skeleton for all of that, on every launch. What it shows changes slowly, so
 * the last answer is kept and shown at once while the current one is fetched.
 */

export type UsageDay = {
  date: string;
  totalTokens: number;
  modelCalls: number;
};

export type HomeUsageInsights = {
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

export const HOME_USAGE_STORAGE_KEY = "loom.home.usageInsights.v1";
/** Older than this, the heatmap would end well before today; show a skeleton instead. */
export const HOME_USAGE_MAX_AGE_DAYS = 14;

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isDateText = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

/** Keeps only what the card reads, or null when the answer is not usable. */
export function trimHomeUsage(value: unknown): HomeUsageInsights | null {
  if (!isRecord(value)) return null;
  const { range, totals, streaks, peakDay, days } = value;
  if (!isRecord(range) || !isDateText(range.startDate) || !isDateText(range.endDate) || !isNumber(range.totalTokens)) return null;
  if (!isRecord(totals) || !isNumber(totals.activeDays) || !isNumber(totals.modelCalls)) return null;
  if (!isRecord(streaks) || !isNumber(streaks.current)) return null;
  if (!Array.isArray(days) || days.length === 0) return null;
  const trimmedDays: UsageDay[] = [];
  for (const day of days) {
    if (!isRecord(day) || !isDateText(day.date) || !isNumber(day.totalTokens) || !isNumber(day.modelCalls)) return null;
    trimmedDays.push({ date: day.date, totalTokens: day.totalTokens, modelCalls: day.modelCalls });
  }
  let peak: HomeUsageInsights["peakDay"] = null;
  if (peakDay !== null && peakDay !== undefined) {
    if (!isRecord(peakDay) || !isDateText(peakDay.date) || !isNumber(peakDay.totalTokens)) return null;
    peak = { date: peakDay.date, totalTokens: peakDay.totalTokens };
  }
  return {
    range: { startDate: range.startDate, endDate: range.endDate, totalTokens: range.totalTokens },
    totals: { activeDays: totals.activeDays, modelCalls: totals.modelCalls },
    streaks: { current: streaks.current },
    peakDay: peak,
    days: trimmedDays,
  };
}

function localDayNumber(text: string): number {
  const [year, month, day] = text.split("-").map(Number);
  return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
}

/** The last stored answer, if it is intact and recent enough to put on screen. */
export function readStoredHomeUsage(storage: ReadableStorage | null, now: Date = new Date()): HomeUsageInsights | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(HOME_USAGE_STORAGE_KEY);
    if (!raw) return null;
    const stored = trimHomeUsage(JSON.parse(raw));
    if (!stored) return null;
    const today = localDayNumber(
      `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    );
    return today - localDayNumber(stored.range.endDate) <= HOME_USAGE_MAX_AGE_DAYS ? stored : null;
  } catch {
    return null;
  }
}

export function storeHomeUsage(storage: WritableStorage | null, value: HomeUsageInsights): void {
  if (!storage) return;
  try {
    storage.setItem(HOME_USAGE_STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Storage can be full or unavailable; the card simply starts from a skeleton next time.
  }
}

export function homeUsageStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

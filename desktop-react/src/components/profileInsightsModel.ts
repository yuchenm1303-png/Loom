// Pure helpers behind the usage profile (ProfileInsightsPage) and the home
// token heatmap (HomeTokenActivity). No React or DOM here, so node --test can
// run them directly (scripts/tests/profile-insights.test.mjs).

export type HeatCell<T> = {
  date: string;
  /** null for padding days before the range starts or after it ends. */
  day: T | null;
};

export function parseLocalDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, Math.max(0, month - 1), day || 1);
}

export function localDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Whole calendar days from `from` to `to` (both YYYY-MM-DD, local). */
export function daysBetween(from: string, to: string): number {
  return Math.round((parseLocalDate(to).getTime() - parseLocalDate(from).getTime()) / 86_400_000);
}

function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

/**
 * Monday-first week columns covering `startDate`..`endDate`. The first and last
 * columns are padded to whole weeks; padding cells carry `day: null`.
 */
export function buildWeeks<T extends { date: string }>(
  days: readonly T[],
  startDate: string,
  endDate: string,
): HeatCell<T>[][] {
  if (!days.length) return [];
  const byDate = new Map(days.map((day) => [day.date, day]));
  const cursor = parseLocalDate(startDate);
  cursor.setDate(cursor.getDate() - mondayIndex(cursor));
  const end = parseLocalDate(endDate);
  const weeks: HeatCell<T>[][] = [];
  while (cursor <= end) {
    const week: HeatCell<T>[] = [];
    for (let row = 0; row < 7; row += 1) {
      const key = localDateKey(cursor);
      week.push({ date: key, day: byDate.get(key) ?? null });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }
  return weeks;
}

export type MonthMarker = { column: number; date: Date; isYearStart: boolean };

/**
 * One marker per month, on the first column whose first in-range day falls in
 * that month (the GitHub convention). The partial month at the left edge only
 * keeps its marker when the next one is at least `minGap` columns away, so the
 * two labels cannot collide.
 */
export function monthMarkers<T>(weeks: readonly HeatCell<T>[][], minGap = 3): MonthMarker[] {
  const markers: MonthMarker[] = [];
  let previous = "";
  weeks.forEach((week, column) => {
    const first = week.find((cell) => cell.day);
    if (!first) return;
    const date = parseLocalDate(first.date);
    const key = `${date.getFullYear()}-${date.getMonth()}`;
    if (key === previous) return;
    previous = key;
    markers.push({ column, date, isYearStart: date.getMonth() === 0 });
  });
  if (markers.length > 1 && markers[1].column - markers[0].column < minGap) markers.shift();
  return markers;
}

export type HeatLevel = 0 | 1 | 2 | 3 | 4;

/**
 * Intensity by percentile rank among active days rather than by raw volume.
 * Token counts span several orders of magnitude, so a volume scale paints
 * nearly every active day at the top two levels; ranks spread them evenly.
 * Equal values share a level, and the busiest day is always level 4.
 */
export function heatScale(values: readonly number[]): (value: number) => HeatLevel {
  const active = values.filter((value) => value > 0).sort((a, b) => a - b);
  return (value: number) => {
    if (!(value > 0) || !active.length) return 0;
    let low = 0;
    let high = active.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (active[mid] <= value) low = mid + 1;
      else high = mid;
    }
    return Math.min(4, Math.max(1, Math.ceil((low / active.length) * 4))) as HeatLevel;
  };
}

/**
 * Compact counts ("1.7亿", "366.8万", "5497万", "1.5K"): at most one decimal
 * and at most four significant digits, whichever is coarser.
 */
export function formatCompact(value: number, locale: string): string {
  const safe = Math.max(0, Math.round(Number(value) || 0));
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
    maximumSignificantDigits: 4,
    roundingPriority: "lessPrecision",
  } as Intl.NumberFormatOptions).format(safe);
}

export function formatDuration(seconds: number, zh: boolean): string {
  const safe = Math.max(0, Math.round(seconds || 0));
  if (safe < 60) return zh ? `${safe} 秒` : `${safe}s`;
  const minutes = Math.floor(safe / 60);
  if (minutes < 60) {
    const rest = safe % 60;
    return zh ? `${minutes} 分 ${rest} 秒` : `${minutes}m ${rest}s`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return zh ? `${hours} 小时 ${mins} 分` : `${hours}h ${mins}m`;
}

export function hourLabel(hour: number, zh: boolean): string {
  if (!zh) {
    const suffix = hour < 12 ? "AM" : "PM";
    return `${hour % 12 || 12} ${suffix}`;
  }
  if (hour < 5) return `凌晨 ${hour} 点`;
  if (hour < 8) return `早上 ${hour} 点`;
  if (hour < 12) return `上午 ${hour} 点`;
  if (hour < 14) return `中午 ${hour === 12 ? 12 : hour - 12} 点`;
  if (hour < 18) return `下午 ${hour - 12} 点`;
  return `晚上 ${hour - 12} 点`;
}

/** "今天", "昨天", "3 天前", else null so the caller can print a date. */
export function relativeDayLabel(date: string, today: string, zh: boolean): string | null {
  const age = daysBetween(date, today);
  if (age === 0) return zh ? "今天" : "Today";
  if (age === 1) return zh ? "昨天" : "Yesterday";
  if (age > 1 && age < 7) return zh ? `${age} 天前` : `${age} days ago`;
  return null;
}

// Display names for Loom's built-in tools. Unknown tools keep their raw id.
const TOOL_LABELS: Record<string, readonly [string, string]> = {
  exec: ["运行命令", "Run command"],
  run_workspace_command: ["运行命令", "Run command"],
  exec_wait: ["等待命令", "Wait for command"],
  exec_terminate: ["结束命令", "Stop command"],
  exec_interrupt: ["中断命令", "Interrupt command"],
  read_workspace_text: ["读取文件", "Read file"],
  write_workspace_text: ["写入文件", "Write file"],
  replace_workspace_text: ["替换文本", "Replace text"],
  apply_patch: ["应用补丁", "Apply patch"],
  search_workspace_text: ["搜索文件内容", "Search files"],
  list_workspace_files: ["浏览目录", "List files"],
  list_workspace_processes: ["查看进程", "List processes"],
  web_search: ["网页搜索", "Web search"],
  tool_search: ["查找工具", "Find tools"],
  browser_open: ["打开网页", "Open page"],
  browser_navigate: ["打开网页", "Open page"],
  browser_click: ["网页点击", "Click in page"],
  browser_click_at: ["网页点击", "Click in page"],
  browser_type: ["网页输入", "Type in page"],
  browser_send_text: ["网页输入", "Type in page"],
  browser_press: ["网页按键", "Press key"],
  browser_screenshot: ["网页截图", "Page screenshot"],
  browser_state: ["读取网页状态", "Read page state"],
  browser_status: ["浏览器状态", "Browser status"],
  browser_switch_tab: ["切换标签页", "Switch tab"],
  browser_scroll: ["滚动网页", "Scroll page"],
  browser_wait: ["等待网页", "Wait for page"],
  browser_eval: ["运行网页脚本", "Run page script"],
  computer_action: ["操作电脑", "Computer action"],
  computer_step: ["操作电脑", "Computer action"],
  computer_observe: ["观察屏幕", "Observe screen"],
  computer_status: ["电脑状态", "Computer status"],
  computer_run_task: ["电脑任务", "Computer task"],
  spawn_agent: ["派出子代理", "Spawn sub-agent"],
  wait_agent: ["等待子代理", "Wait for sub-agent"],
  send_agent_message: ["协调子代理", "Message sub-agent"],
  search_memory: ["搜索记忆", "Search memory"],
  code_mode: ["代码模式", "Code mode"],
};

export function toolLabel(name: string, zh: boolean): string | null {
  const entry = TOOL_LABELS[name];
  return entry ? entry[zh ? 0 : 1] : null;
}

/**
 * What a turn's tool work is made of, independent of how it is drawn: the rows (one per step, with an
 * exec call and the process it starts folded into one), their state, timing and detail, and the counts
 * a summary line reports. Components read this; none of it touches the DOM.
 */
import type { TranscriptItem } from "../types/loom";
import { artifactRenderer } from "../artifactRenderers";
import { isActivityItem } from "./executionSequence";
import { activityToolLabel } from "./ToolIdentity";
import {
  commandFromItem,
  describeActivity,
  isCommandTool,
  isEditTool,
  normalizedToolName,
  type ActivityCategory,
  type ActivityDescription,
  type ProcessSummaryParts,
  type RuntimeCopy,
} from "./runtimeCopy";
import { firstFailureLine, lastOutputLine, parseTime } from "./stepFormat";

/**
 * One visible task row. A tool call that only launches a process or writes a
 * file (`exec`, `write_workspace_text`, ...) and the process/diff it produces
 * are the same step, so they share one row keyed by the call that appeared
 * first. The row keeps its DOM, motion and disclosure state while the outcome
 * item arrives, instead of swapping "使用 exec" for "运行 …" a moment later.
 */
export interface ActivityRowModel {
  key: string;
  item: TranscriptItem;
  wrapper: TranscriptItem | null;
}

/** Per-step view of the cumulative turn diff snapshots. */
export interface FileEditDelta {
  paths: string[];
  diff: string;
  added: number;
  removed: number;
}

export interface TurnProcessBreakdown extends ProcessSummaryParts {
  added: number;
  removed: number;
}

/** How a step looks: the one thing its node says. */
export type StepTone = "running" | "waiting" | "done" | "failed" | "stopped" | "caution";

export function itemStatus(item: TranscriptItem): string {
  if (item.type === "tool_call") {
    const result = item.result as Record<string, unknown> | undefined;
    const evidence = result?.action_evidence as Record<string, unknown> | undefined;
    if (result?.execution_status === "not_executed" || evidence?.execution_status === "not_executed") return "not_executed";
    if (result?.effect === "uncertain") return "uncertain";
    if (item.status === "failed" && ["stale_observation", "stale_element"].includes(String(result?.error_code || ""))) return "refresh_required";
  }
  if (item.type === "file_edit") return item.status || "changed";
  return item.status || "completed";
}

export function isActiveActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments", "waiting", "waiting_approval", "pending"].includes(status);
}

export function isExecutingActivityStatus(status: string): boolean {
  return ["started", "running", "streaming", "streaming_arguments"].includes(status);
}

export function isFailureStatus(status: string): boolean {
  return status === "failed" || status === "denied" || status === "cancelled" || status === "interrupted";
}

/** The wrapper's verdict wins when it reports a problem the outcome item cannot. */
export function rowStatus(row: ActivityRowModel): string {
  const status = itemStatus(row.item);
  if (!row.wrapper || row.wrapper === row.item) return status;
  const wrapper = itemStatus(row.wrapper);
  if (isFailureStatus(wrapper) || ["not_executed", "uncertain", "refresh_required", "waiting", "waiting_approval"].includes(wrapper)) return wrapper;
  return status;
}

export function statusTone(status: string): StepTone {
  if (isExecutingActivityStatus(status)) return "running";
  if (status === "waiting" || status === "waiting_approval" || status === "pending") return "waiting";
  if (status === "failed" || status === "denied") return "failed";
  if (status === "cancelled" || status === "interrupted" || status === "not_executed") return "stopped";
  if (status === "uncertain" || status === "refresh_required") return "caution";
  return "done";
}

export function rowTone(row: ActivityRowModel): StepTone {
  return statusTone(rowStatus(row));
}

export function buildActivityRows(items: TranscriptItem[]): ActivityRowModel[] {
  const rows: ActivityRowModel[] = [];
  const commandWrappers: ActivityRowModel[] = [];
  const editWrappers: ActivityRowModel[] = [];

  for (const item of items) {
    if (item.type === "assistant_message") {
      commandWrappers.length = 0;
      editWrappers.length = 0;
    }
    if (item.type === "tool_call" && (isCommandTool(item) || isEditTool(item))) {
      const row = { key: item.id, item, wrapper: item };
      rows.push(row);
      (isCommandTool(item) ? commandWrappers : editWrappers).push(row);
      continue;
    }
    if (item.type === "process" && commandWrappers.length) {
      const command = commandFromItem(item);
      const exact = commandWrappers.findIndex((row) => commandFromItem(row.wrapper) === command);
      const [row] = commandWrappers.splice(exact >= 0 ? exact : 0, 1);
      row.item = item;
      continue;
    }
    if (item.type === "file_edit") {
      const index = editWrappers.findIndex((row) => !isFailureStatus(itemStatus(row.item)));
      if (index >= 0) {
        const [row] = editWrappers.splice(index, 1);
        row.item = item;
        continue;
      }
    }
    rows.push({ key: item.id, item, wrapper: null });
  }
  return rows;
}

/** Tool neighbours retire together; commentary supplies independent viewport anchors. */
export function activitySegments(rows: ActivityRowModel[]): ActivityRowModel[][] {
  const segments: ActivityRowModel[][] = [];
  for (const row of rows) {
    const previous = segments[segments.length - 1];
    if (isActivityItem(row.item) && previous && isActivityItem(previous[0].item)) previous.push(row);
    else segments.push([row]);
  }
  return segments;
}

const VISUAL_ARTIFACTS = new Set(["web", "image", "pdf", "video", "audio"]);

/** The last written file worth looking at rather than reading as a diff. */
export function visualArtifactPath(paths: readonly unknown[]): string {
  for (let index = paths.length - 1; index >= 0; index -= 1) {
    const path = String(paths[index] ?? "").trim();
    if (path && VISUAL_ARTIFACTS.has(artifactRenderer(path).kind)) return path;
  }
  return "";
}

export function diffStats(diff?: string): { added: number; removed: number } {
  if (!diff) return { added: 0, removed: 0 };
  let added = 0;
  let removed = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added += 1;
    else if (line.startsWith("-") && !line.startsWith("---")) removed += 1;
  }
  return { added, removed };
}

/** Split a unified diff into per-file chunks (path -> chunk text). */
function splitDiffByFile(diff: string): Map<string, string> {
  const files = new Map<string, string>();
  const lines = diff.split("\n");
  let path = "";
  let chunk: string[] = [];
  let sawHunk = false;
  const flush = () => {
    if (chunk.length && path) files.set(path, chunk.join("\n"));
    chunk = [];
    sawHunk = false;
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const git = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    const pair = line.startsWith("--- ") && lines[index + 1]?.startsWith("+++ ");
    if (git || (pair && (sawHunk || !chunk.length || !path))) {
      if (!(pair && chunk.length && !sawHunk && path)) flush();
      if (git) path = git[2].trim();
    }
    if (pair) {
      const target = lines[index + 1].slice(4).trim().replace(/^b\//, "");
      const source = line.slice(4).trim().replace(/^a\//, "");
      path = target && target !== "/dev/null" ? target : source;
    }
    if (line.startsWith("@@")) sawHunk = true;
    chunk.push(line);
  }
  flush();
  return files;
}

export function fileEditDeltas(items: TranscriptItem[]): Map<string, FileEditDelta> {
  const deltas = new Map<string, FileEditDelta>();
  const previousChunks = new Map<string, string>();
  const previousPaths = new Set<string>();
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const diff = String(item.diff ?? "");
    const paths = (item.paths ?? []).map(String).map((path) => path.trim()).filter(Boolean);
    const chunks = splitDiffByFile(diff);
    const changed = [...chunks].filter(([path, text]) => previousChunks.get(path) !== text);
    const changedPaths = new Set(changed.map(([path]) => path));
    for (const path of paths) {
      if (!previousPaths.has(path) && ![...chunks.keys()].some((known) => known.endsWith(path) || path.endsWith(known))) changedPaths.add(path);
    }
    chunks.forEach((text, path) => previousChunks.set(path, text));
    paths.forEach((path) => previousPaths.add(path));
    if (!changedPaths.size) {
      deltas.set(item.id, { paths, diff, ...diffStats(diff) });
      continue;
    }
    const deltaDiff = changed.length ? changed.map(([, text]) => text).join("\n") : diff;
    deltas.set(item.id, { paths: [...changedPaths], diff: deltaDiff, ...diffStats(deltaDiff) });
  }
  return deltas;
}

function isFileReadTool(item: TranscriptItem): boolean {
  if (item.type !== "tool_call") return false;
  const name = normalizedToolName(item);
  if (!name) return false;
  const hasReadVerb = /(^|_)(read|open|fetch|get|cat)(_|$)/.test(name);
  const hasFileObject = /(^|_)(file|files|workspace|text|document|blob)(_|$)/.test(name);
  return hasReadVerb && hasFileObject;
}

function collectReadPaths(value: unknown, paths: Set<string>, keyHint = "") {
  if (typeof value === "string") {
    if (/(^|_)(path|paths|file|files|filename|filepath|file_path)$/.test(keyHint) && value.trim()) paths.add(value.trim());
    return;
  }
  if (Array.isArray(value)) {
    for (const entry of value) collectReadPaths(entry, paths, keyHint);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    collectReadPaths(entry, paths, key.trim().toLowerCase());
  }
}

function isBrowserScreenshotActivity(item: TranscriptItem): boolean {
  return item.type === "tool_call"
    && String(item.toolName ?? "").trim().toLowerCase() === "browser_screenshot";
}

export function browserScreenshotPaths(item: TranscriptItem): string[] {
  if (!isBrowserScreenshotActivity(item)) return [];
  const result = item.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return [];
  const path = String((result as Record<string, unknown>).path ?? "").trim();
  if (!path || !/\.png$/i.test(path)) return [];
  return [path];
}

export function turnProcessBreakdown(items: TranscriptItem[], copyForSummary: RuntimeCopy): TurnProcessBreakdown {
  const rows = buildActivityRows(items.filter(isActivityItem));
  const editedPaths = new Set<string>();
  const readPaths = new Set<string>();
  let anonymousEdits = 0;
  let anonymousReads = 0;
  let commands = 0;
  let imagesViewed = 0;
  let tools = 0;
  let searches = 0;
  let lastDiff = "";

  for (const row of rows) {
    const source = row.wrapper ?? row.item;
    if (row.item.type === "process" || isCommandTool(source)) {
      commands += 1;
      continue;
    }
    if (row.item.type === "file_edit" || isEditTool(source)) {
      const paths = (row.item.type === "file_edit" ? row.item.paths ?? [] : []).map(String).map((path) => path.trim()).filter(Boolean);
      if (paths.length) paths.forEach((path) => editedPaths.add(path));
      else if (row.item.type === "file_edit") anonymousEdits += 1;
      if (row.item.type === "file_edit" && String(row.item.diff ?? "").trim()) lastDiff = String(row.item.diff);
      continue;
    }
    const category = describeActivity(row.item, row.wrapper, itemStatus(row.item), copyForSummary, { fallbackToolLabel: "", screenshotCount: 0 }).category;
    if (category === "search" || category === "web") { searches += 1; continue; }
    if (isFileReadTool(row.item)) {
      const before = readPaths.size;
      collectReadPaths(row.item.arguments, readPaths);
      if (readPaths.size === before) anonymousReads += 1;
      continue;
    }
    const screenshots = browserScreenshotPaths(row.item).length;
    if (screenshots) {
      imagesViewed += screenshots;
      continue;
    }
    tools += 1;
  }

  // Diff items are cumulative turn snapshots: the latest one is the turn's diff.
  const { added, removed } = diffStats(lastDiff);
  return {
    commands,
    filesEdited: editedPaths.size + anonymousEdits,
    filesRead: readPaths.size + anonymousReads,
    imagesViewed,
    searches,
    tools,
    added,
    removed,
  };
}

export function hasActivityDetail(item: TranscriptItem): boolean {
  if (browserScreenshotPaths(item).length) return true;
  if (item.type === "process") return Boolean(String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim());
  if (item.type === "file_edit") return Boolean(String(item.diff ?? "").trim());
  if (String(item.content ?? "").trim()) return true;
  if (String(item.stdout ?? "").trim() || String(item.stderr ?? "").trim()) return true;
  return item.arguments !== undefined;
}

export function activityDetail(item: TranscriptItem): string {
  if (browserScreenshotPaths(item).length) return "";
  if (item.type === "process") {
    const stdout = String(item.stdout ?? "");
    const stderr = String(item.stderr ?? "");
    return `${stdout}${stderr ? `${stdout ? "\n" : ""}${stderr}` : ""}`.trim();
  }
  if (item.type === "file_edit") return String(item.diff ?? "").trim();
  if (item.content) return String(item.content);
  if (item.stdout || item.stderr) return `${String(item.stdout ?? "")}${item.stderr ? `\n${String(item.stderr)}` : ""}`.trim();
  if (item.arguments !== undefined) {
    try {
      return JSON.stringify(item.arguments, null, 2);
    } catch {
      return String(item.arguments);
    }
  }
  return "";
}

export function rowHasDetail(row: ActivityRowModel): boolean {
  return hasActivityDetail(row.item) || Boolean(row.wrapper && row.wrapper !== row.item && hasActivityDetail(row.wrapper));
}

export function rowDetail(row: ActivityRowModel, delta?: FileEditDelta): string {
  if (row.item.type === "file_edit" && delta?.diff.trim()) return delta.diff.trim();
  const primary = activityDetail(row.item);
  if (primary || !row.wrapper || row.wrapper === row.item) return primary;
  return activityDetail(row.wrapper);
}

/** The item whose identity (icon, family) represents a row from its first frame. */
export function identitySource(row: ActivityRowModel): TranscriptItem {
  const source = row.wrapper ?? row.item;
  if (row.item.type === "process" || isCommandTool(source)) return row.item.type === "process" ? row.item : { ...source, type: "process" };
  if (row.item.type === "file_edit" || isEditTool(source)) return row.item.type === "file_edit" ? row.item : { ...source, type: "file_edit" };
  return source;
}

export function describeRow(row: ActivityRowModel, copy: RuntimeCopy, delta?: FileEditDelta): ActivityDescription {
  const status = rowStatus(row);
  const screenshots = browserScreenshotPaths(row.item).length;
  const primary = row.item.type === "file_edit" && delta ? { ...row.item, paths: delta.paths } : row.item;
  return describeActivity(primary, row.wrapper, status, copy, {
    fallbackToolLabel: activityToolLabel(row.wrapper ?? row.item),
    screenshotCount: screenshots,
  });
}

export function liveHintKind(row: ActivityRowModel, status: string): "approval" | "waiting" | "command" | "edit" | "tool" {
  if (status === "waiting_approval") return "approval";
  if (status === "waiting" || status === "pending") return "waiting";
  const source = row.wrapper ?? row.item;
  if (row.item.type === "process" || isCommandTool(source)) return "command";
  if (row.item.type === "file_edit" || isEditTool(source)) return "edit";
  return "tool";
}

/** When a step started and, once it is over, ended. The call that asked for it starts the clock. */
export function rowSpan(row: ActivityRowModel): { start: number | null; end: number | null } {
  const starts = [row.wrapper?.createdAt, row.item.createdAt].map(parseTime).filter((value): value is number => value !== null);
  const ends = [row.item.updatedAt, row.wrapper?.updatedAt, row.item.createdAt].map(parseTime).filter((value): value is number => value !== null);
  return { start: starts.length ? Math.min(...starts) : null, end: ends.length ? Math.max(...ends) : null };
}

/** The last line a running command printed: its output, or what it said on the error stream when it has no output. */
export function rowTail(row: ActivityRowModel): string {
  if (row.item.type !== "process") return "";
  return lastOutputLine(String(row.item.stdout ?? ""), String(row.item.stderr ?? ""));
}

/** Why a step failed, in the reader's first glance: the first line it printed or said. */
export function rowFailure(row: ActivityRowModel): string {
  const status = rowStatus(row);
  if (status !== "failed" && status !== "denied") return "";
  const sources = [row.item, row.wrapper].filter((item): item is TranscriptItem => Boolean(item));
  return firstFailureLine(
    ...sources.map((item) => String(item.stderr ?? "")),
    ...sources.map((item) => String(item.error ?? "")),
    ...sources.map((item) => (typeof item.content === "string" ? item.content : "")),
    ...sources.map((item) => {
      const result = item.result as Record<string, unknown> | undefined;
      return typeof result?.message === "string" ? result.message : typeof result?.error === "string" ? result.error : "";
    }),
  );
}

export function groupCategories(rows: ActivityRowModel[], copy: RuntimeCopy, deltas: ReadonlyMap<string, FileEditDelta>): ActivityCategory[] {
  const categories: ActivityCategory[] = [];
  for (const row of rows) {
    const category = describeRow(row, copy, deltas.get(row.item.id)).category;
    if (!categories.includes(category)) categories.push(category);
  }
  return categories;
}

/** What a stage did, in counts: "运行 2 条命令 · 操作浏览器 3 次". This is the one line of a folded stage. */
export function groupSummary(rows: ActivityRowModel[], copy: RuntimeCopy): string {
  const kinds = new Map<string, { category: ActivityCategory; count: number; toolName: string }>();
  for (const row of rows) {
    const { category } = describeRow(row, copy);
    const toolName = String((row.wrapper ?? row.item).toolName ?? "");
    const key = category === "tool" && toolName === "tool_search" ? "tool_search" : category;
    const kind = kinds.get(key);
    if (kind) kind.count += 1;
    else kinds.set(key, { category, count: 1, toolName });
  }
  const phrases = [...kinds.values()].slice(0, 3).map((kind) => copy.clusterTitle(kind.category, kind.count, kind.toolName));
  return phrases.join(" · ") + (kinds.size > 3 ? " …" : "");
}

/** Time from the first step's start to the last step's end, or 0 when the timestamps are missing. */
export function stageDuration(rows: ActivityRowModel[]): number {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const row of rows) {
    const span = rowSpan(row);
    if (span.start !== null) start = Math.min(start, span.start);
    if (span.end !== null) end = Math.max(end, span.end);
  }
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0;
}

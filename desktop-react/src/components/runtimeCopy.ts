/**
 * Runtime copy for the live conversation.
 *
 * Every string the transcript shows while Loom works lives here, in both UI
 * languages, so tense, wording and punctuation stay consistent across the
 * thinking capsule, task rows, group titles, run strip and turn summary.
 * Tool rows describe what happened ("已读取 src/App.tsx"), never the raw tool
 * identifier, unless the tool is unknown.
 */
import { useMemo } from "react";
import { useLoomLanguage, type LoomLanguage } from "../i18n";
import type { TranscriptItem } from "../types/loom";

export type RuntimeLanguage = "zh" | "en";

export type ActivityCategory =
  | "command"
  | "edit"
  | "read"
  | "list"
  | "search"
  | "web"
  | "browser"
  | "computer"
  | "agent"
  | "plan"
  | "memory"
  | "image"
  | "tool";

export type ActivityTense = "active" | "done" | "failed" | "waiting" | "denied" | "stopped" | "skipped" | "uncertain" | "refresh";

interface EnglishVerb {
  ing: string;
  past: string;
  base: string;
}

interface VerbSpec {
  zh: string;
  en: EnglishVerb;
}

export interface ActivityDescription {
  category: ActivityCategory;
  verb: string;
  target: string;
  /** Full value for the tooltip when the visible target is shortened. */
  title?: string;
  code?: boolean;
}

const VERBS = {
  run: { zh: "运行", en: { ing: "Running", past: "Ran", base: "run" } },
  edit: { zh: "编辑", en: { ing: "Editing", past: "Edited", base: "edit" } },
  write: { zh: "写入", en: { ing: "Writing", past: "Wrote", base: "write" } },
  read: { zh: "读取", en: { ing: "Reading", past: "Read", base: "read" } },
  browse: { zh: "浏览", en: { ing: "Listing", past: "Listed", base: "list" } },
  search: { zh: "搜索", en: { ing: "Searching", past: "Searched", base: "search" } },
  webSearch: { zh: "联网搜索", en: { ing: "Searching the web for", past: "Searched the web for", base: "search the web for" } },
  open: { zh: "打开", en: { ing: "Opening", past: "Opened", base: "open" } },
  switchTab: { zh: "切换到", en: { ing: "Switching to", past: "Switched to", base: "switch to" } },
  click: { zh: "点击", en: { ing: "Clicking", past: "Clicked", base: "click" } },
  type: { zh: "输入", en: { ing: "Typing", past: "Typed", base: "type" } },
  press: { zh: "按下", en: { ing: "Pressing", past: "Pressed", base: "press" } },
  scroll: { zh: "滚动", en: { ing: "Scrolling", past: "Scrolled", base: "scroll" } },
  wait: { zh: "等待", en: { ing: "Waiting for", past: "Waited for", base: "wait for" } },
  inspect: { zh: "查看", en: { ing: "Checking", past: "Checked", base: "check" } },
  capture: { zh: "截取", en: { ing: "Capturing", past: "Captured", base: "capture" } },
  select: { zh: "选择", en: { ing: "Selecting", past: "Selected", base: "select" } },
  upload: { zh: "上传", en: { ing: "Uploading", past: "Uploaded", base: "upload" } },
  close: { zh: "关闭", en: { ing: "Closing", past: "Closed", base: "close" } },
  back: { zh: "返回", en: { ing: "Going back on", past: "Went back on", base: "go back on" } },
  refresh: { zh: "刷新", en: { ing: "Reloading", past: "Reloaded", base: "reload" } },
  evaluate: { zh: "执行", en: { ing: "Running", past: "Ran", base: "run" } },
  operate: { zh: "操作", en: { ing: "Using", past: "Used", base: "use" } },
  start: { zh: "启动", en: { ing: "Starting", past: "Started", base: "start" } },
  message: { zh: "发送消息给", en: { ing: "Messaging", past: "Messaged", base: "message" } },
  update: { zh: "更新", en: { ing: "Updating", past: "Updated", base: "update" } },
  find: { zh: "查找", en: { ing: "Finding", past: "Found", base: "find" } },
  recall: { zh: "检索", en: { ing: "Searching", past: "Searched", base: "search" } },
  calculate: { zh: "计算", en: { ing: "Calculating", past: "Calculated", base: "calculate" } },
  stop: { zh: "终止", en: { ing: "Stopping", past: "Stopped", base: "stop" } },
  interrupt: { zh: "中断", en: { ing: "Interrupting", past: "Interrupted", base: "interrupt" } },
  prepare: { zh: "准备", en: { ing: "Preparing", past: "Prepared", base: "prepare" } },
  mark: { zh: "标记", en: { ing: "Marking", past: "Marked", base: "mark" } },
  call: { zh: "调用", en: { ing: "Using", past: "Used", base: "use" } },
} satisfies Record<string, VerbSpec>;

type VerbKey = keyof typeof VERBS;

interface ToolSpec {
  category: ActivityCategory;
  verb: VerbKey;
  target(args: Record<string, unknown>, item: TranscriptItem, lang: RuntimeLanguage): string;
  code?: boolean;
}

const COMMAND_TOOL = /^(exec|execute|exec_command|shell|run_command|run-command|command|powershell|pwsh|bash|sh|cmd)$/;
const EDIT_TOOL = /^(write_workspace_text|replace_workspace_text|write_file|write-file|edit_file|edit-file|apply_patch|apply-patch|patch_file|patch-file|replace_text|replace-text)$/;

export function normalizedToolName(item: TranscriptItem): string {
  return String(item.toolName ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
}

export function isCommandTool(item: TranscriptItem): boolean {
  return item.type === "tool_call" && COMMAND_TOOL.test(normalizedToolName(item));
}

export function isEditTool(item: TranscriptItem): boolean {
  return item.type === "tool_call" && EDIT_TOOL.test(normalizedToolName(item));
}

function args(item: TranscriptItem | null | undefined): Record<string, unknown> {
  const value = item?.arguments;
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
}

function clip(value: string, max: number): string {
  const graphemes = Array.from(value.replace(/\s+/g, " ").trim());
  return graphemes.length > max ? `${graphemes.slice(0, max - 1).join("")}…` : graphemes.join("");
}

function quoted(value: string, lang: RuntimeLanguage, max = 36): string {
  const clean = clip(value, max);
  if (!clean) return "";
  return lang === "zh" ? `“${clean}”` : `“${clean}”`;
}

/** Workspace-relative paths stay intact; absolute ones keep their last two parts. */
export function displayPath(path: string): string {
  const clean = path.trim().replace(/\\/g, "/");
  if (!clean) return "";
  const absolute = /^[A-Za-z]:\//.test(clean) || clean.startsWith("/") || clean.startsWith("~/");
  if (!absolute) return clean.replace(/^\.\//, "");
  const parts = clean.split("/").filter(Boolean);
  return parts.slice(-2).join("/");
}

function displayUrl(value: string): string {
  try {
    const url = new URL(value);
    const path = url.pathname === "/" ? "" : url.pathname.replace(/\/$/, "");
    return `${url.host}${path}`;
  } catch {
    return clip(value, 60);
  }
}

const SHELL_WRAPPERS: Array<{ shell: RegExp; flag: RegExp }> = [
  { shell: /^(powershell|pwsh)(\.exe)?$/i, flag: /^-(c|command)$/i },
  { shell: /^cmd(\.exe)?$/i, flag: /^\/(c|k)$/i },
  { shell: /^(ba|z|)sh$/i, flag: /^-(c|lc|ic)$/i },
];

/** Show the script a shell wrapper runs instead of its launcher flags. */
export function commandDisplay(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (!Array.isArray(value)) return "";
  const argv = value.map(String);
  const program = (argv[0] ?? "").split(/[\\/]/).pop() ?? "";
  for (const { shell, flag } of SHELL_WRAPPERS) {
    if (!shell.test(program)) continue;
    const index = argv.findIndex((part, position) => position > 0 && flag.test(part));
    if (index > 0 && index < argv.length - 1) return argv.slice(index + 1).join(" ").trim();
  }
  return argv.join(" ").trim();
}

export function commandFromItem(item: TranscriptItem | null | undefined): string {
  if (!item) return "";
  if (item.type === "process") {
    return commandDisplay(item.argv) || text(item.command) || text(item.toolName);
  }
  const parameters = args(item);
  return commandDisplay(parameters.argv)
    || commandDisplay(parameters.command)
    || commandDisplay(parameters.cmd)
    || commandDisplay(parameters.script);
}

function editTargets(item: TranscriptItem | null | undefined): string[] {
  if (!item) return [];
  if (item.type === "file_edit") return (item.paths ?? []).map(String).filter(Boolean);
  const parameters = args(item);
  const paths = new Set<string>();
  const direct = text(parameters.path) || text(parameters.file_path) || text(parameters.filename);
  if (direct) paths.add(direct);
  if (Array.isArray(parameters.changes)) {
    for (const change of parameters.changes) {
      const path = change && typeof change === "object" ? text((change as Record<string, unknown>).path) : "";
      if (path) paths.add(path);
    }
  }
  const patch = text(parameters.patch);
  if (patch) {
    for (const match of patch.matchAll(/^\*\*\* (?:Update|Add|Delete) File: (.+)$/gm)) paths.add(match[1].trim());
  }
  return [...paths];
}

export function pathsLabel(paths: string[], lang: RuntimeLanguage): string {
  const visible = paths.map(displayPath).filter(Boolean);
  if (!visible.length) return lang === "zh" ? "工作区文件" : "workspace files";
  if (visible.length === 1) return visible[0];
  if (visible.length === 2) return `${visible[0]}, ${visible[1]}`;
  return lang === "zh" ? `${visible[0]} 等 ${visible.length} 个文件` : `${visible[0]} and ${visible.length - 1} more`;
}

const fixed = (zh: string, en: string) => (_: Record<string, unknown>, __: TranscriptItem, lang: RuntimeLanguage) => (lang === "zh" ? zh : en);

const TOOL_SPECS: Record<string, ToolSpec> = {
  read_workspace_text: { category: "read", verb: "read", target: (a, _, lang) => displayPath(text(a.path)) || fixed("文件", "a file")(a, _, lang) },
  read_file: { category: "read", verb: "read", target: (a, _, lang) => displayPath(text(a.path) || text(a.file_path)) || fixed("文件", "a file")(a, _, lang) },
  read_durable_tool_result: { category: "read", verb: "read", target: fixed("完整输出", "the full output") },
  list_workspace_files: { category: "list", verb: "browse", target: (a, _, lang) => displayPath(text(a.path)) || (lang === "zh" ? "工作区" : "the workspace") },
  search_workspace_text: { category: "search", verb: "search", target: (a, _, lang) => quoted(text(a.query) || text(a.pattern), lang) },
  get_run_scratch_dir: { category: "tool", verb: "prepare", target: fixed("临时目录", "a scratch folder") },
  list_workspace_processes: { category: "command", verb: "inspect", target: fixed("运行中的命令", "running commands") },
  exec_wait: { category: "command", verb: "wait", target: fixed("命令结束", "a command to finish") },
  exec_terminate: { category: "command", verb: "stop", target: fixed("命令", "a command") },
  exec_interrupt: { category: "command", verb: "interrupt", target: fixed("命令", "a command") },
  code_mode: { category: "command", verb: "run", target: fixed("代码片段", "a code snippet") },
  web_search: { category: "web", verb: "webSearch", target: (a, _, lang) => quoted(text(a.query), lang) },
  web_search_status: { category: "web", verb: "inspect", target: fixed("联网搜索状态", "web search status") },
  browser_open: { category: "browser", verb: "open", target: (a, _, lang) => (text(a.url) ? displayUrl(text(a.url)) : lang === "zh" ? "浏览器" : "the browser") },
  browser_navigate: { category: "browser", verb: "open", target: (a, _, lang) => (text(a.url) ? displayUrl(text(a.url)) : lang === "zh" ? "网页" : "a page") },
  browser_switch_tab: { category: "browser", verb: "switchTab", target: (a, _, lang) => (text(a.url) ? displayUrl(text(a.url)) : lang === "zh" ? "另一个标签页" : "another tab") },
  browser_click: { category: "browser", verb: "click", target: (a, _, lang) => text(a.name) ? quoted(text(a.name), lang, 28) : lang === "zh" ? "页面元素" : "an element" },
  browser_click_at: { category: "browser", verb: "click", target: fixed("页面位置", "a point on the page") },
  browser_type: { category: "browser", verb: "type", target: fixed("文字", "text") },
  browser_send_text: { category: "browser", verb: "type", target: fixed("文字", "text") },
  browser_press: { category: "browser", verb: "press", target: (a, _, lang) => text(a.key) || (lang === "zh" ? "按键" : "a key") },
  browser_scroll: { category: "browser", verb: "scroll", target: fixed("页面", "the page") },
  browser_wait: { category: "browser", verb: "wait", target: fixed("页面就绪", "the page") },
  browser_state: { category: "browser", verb: "read", target: fixed("页面内容", "the page") },
  browser_status: { category: "browser", verb: "inspect", target: fixed("浏览器状态", "browser status") },
  browser_tabs: { category: "browser", verb: "inspect", target: fixed("标签页", "open tabs") },
  browser_screenshot: { category: "image", verb: "capture", target: fixed("页面截图", "a screenshot") },
  browser_select: { category: "browser", verb: "select", target: (a, _, lang) => (text(a.value) ? quoted(text(a.value), lang, 24) : lang === "zh" ? "选项" : "an option") },
  browser_dropdown_options: { category: "browser", verb: "read", target: fixed("下拉选项", "dropdown options") },
  browser_upload: { category: "browser", verb: "upload", target: (a, _, lang) => displayPath(text(a.path)) || (lang === "zh" ? "文件" : "a file") },
  browser_back: { category: "browser", verb: "back", target: fixed("上一页", "the previous page") },
  browser_refresh: { category: "browser", verb: "refresh", target: fixed("页面", "the page") },
  browser_close: { category: "browser", verb: "close", target: fixed("浏览器", "the browser") },
  browser_close_tab: { category: "browser", verb: "close", target: fixed("标签页", "a tab") },
  browser_eval: { category: "browser", verb: "evaluate", target: fixed("页面脚本", "a page script") },
  browser_cookies: { category: "browser", verb: "read", target: fixed("浏览器 Cookie", "browser cookies") },
  browser_storage: { category: "browser", verb: "read", target: fixed("浏览器存储", "browser storage") },
  browser_backends: { category: "browser", verb: "inspect", target: fixed("可用浏览器", "available browsers") },
  computer_action: {
    category: "computer",
    verb: "operate",
    target: (a, _, lang) => {
      const action = a.action && typeof a.action === "object" ? text((a.action as Record<string, unknown>).type) : text(a.action);
      if (action === "screenshot") return lang === "zh" ? "电脑（截屏）" : "the computer (screenshot)";
      return lang === "zh" ? "电脑" : "the computer";
    },
  },
  computer_status: { category: "computer", verb: "inspect", target: fixed("电脑操作状态", "computer status") },
  spawn_agent: { category: "agent", verb: "start", target: (a, _, lang) => text(a.role) ? clip(text(a.role), 32) : lang === "zh" ? "子代理" : "a sub-agent" },
  wait_agent: { category: "agent", verb: "wait", target: fixed("子代理", "a sub-agent") },
  send_agent_message: { category: "agent", verb: "message", target: fixed("子代理", "a sub-agent") },
  list_agents: { category: "agent", verb: "inspect", target: fixed("子代理", "sub-agents") },
  close_agent: { category: "agent", verb: "close", target: fixed("子代理", "a sub-agent") },
  update_plan: { category: "plan", verb: "update", target: fixed("任务计划", "the plan") },
  tool_search: { category: "tool", verb: "find", target: (a, _, lang) => quoted(text(a.query), lang) || (lang === "zh" ? "可用工具" : "tools") },
  search_memory: { category: "memory", verb: "recall", target: (a, _, lang) => quoted(text(a.query), lang) || (lang === "zh" ? "记忆" : "memory") },
  read_memory: { category: "memory", verb: "read", target: fixed("记忆", "a memory") },
  memory_index: { category: "memory", verb: "read", target: fixed("记忆索引", "the memory index") },
  memory_status: { category: "memory", verb: "inspect", target: fixed("记忆状态", "memory status") },
  calculator: { category: "tool", verb: "calculate", target: (a) => clip(text(a.expression), 40), code: true },
  mark_thread_goal: { category: "plan", verb: "mark", target: fixed("目标状态", "the goal") },
  get_thread_state: { category: "tool", verb: "read", target: fixed("对话状态", "conversation state") },
  get_turn_diff: { category: "tool", verb: "read", target: fixed("本轮改动", "this turn's changes") },
  get_sandbox_status: { category: "tool", verb: "inspect", target: fixed("沙箱状态", "sandbox status") },
  github_connection_status: { category: "tool", verb: "inspect", target: fixed("GitHub 连接", "the GitHub connection") },
};

export interface RuntimeCopy {
  lang: RuntimeLanguage;
  thinking: string;
  runPhase: { thinking: string; next: string; reply: string; approval: string; commandOutput: string; agentProgress: string;
    stalled: string; done: string; failed: string; stopped: string; limit: string };
  runSteps(count: number): string;
  runWait(seconds: number): string;
  thoughtProcess: string;
  showReasoning: string;
  hideReasoning: string;
  earlier: string;
  earlierCount(count: number): string;
  earlierToggleLabel(open: boolean, count: number): string;
  earlierToggleTitle(open: boolean): string;
  noteExpand: string;
  noteCollapse: string;
  processToggleTitle(open: boolean): string;
  processSummary(parts: ProcessSummaryParts, fallbackCount: number): string;
  diffLabel(added: number, removed: number): string;
  elapsed(seconds: number | null): string;
  groupTitle(categories: ActivityCategory[], running: boolean): string;
  statusLabel(status: string): string;
  liveHint(kind: "approval" | "waiting" | "command" | "edit" | "tool"): string;
  expandDetails: string;
  collapseDetails: string;
  approvalTitle: string;
  approvalTool: string;
  deny: string;
  allow: string;
  turnFailed: string;
  copy: string;
  copied: string;
  copyMessage: string;
  quoteAnswer: string;
  quoteMessage: string;
  quoteAnswerLabel: string;
  quoteMessageLabel: string;
  editMessage: string;
  editMessageLabel: string;
  helpful: string;
  notHelpful: string;
  helpfulLabel: string;
  notHelpfulLabel: string;
  userActions: string;
  assistantActions: string;
  subAgentsRunning: string;
  subAgentsUsed: string;
  subAgentsOpen: string;
  subAgentsTitle: string;
  openArtifact: string;
  artifactUpdates(path: string, count: number): string;
  imagesViewed(count: number): string;
  imageLoading: string;
  imageUnavailable: string;
  imageAlt(name: string): string;
  decisionRetryPrompt: string;
  guidanceRecap: string;
  activityRegion: string;
  /** Changed-files card under a finished turn. */
  changedFilesTitle(count: number, name: string): string;
  changedFilesRegion: string;
  review: string;
  reviewTitle: string;
  openInReview(path: string): string;
  preview: string;
  previewArtifact(name: string): string;
  previewArtifactTitle(path: string): string;
  /** How a turn's terminal error item reads; a user's own stop is not a failure. */
  turnEnding(error: string): { tone: "stopped" | "error"; text: string };
  verb(spec: VerbKey, tense: ActivityTense): string;
}

const USER_STOP = /^(cancelled by user|turn_cancelled|turn_interrupted|interrupted by user)\.?$/i;
const PROCESS_STOP = /^agent process stopped before the active turn reached a durable terminal state\.?$/i;

export interface ProcessSummaryParts {
  commands: number;
  filesEdited: number;
  filesRead: number;
  imagesViewed: number;
  searches: number;
  tools: number;
}

const ZH_GROUP: Record<ActivityCategory, [string, string]> = {
  command: ["运行", "命令"],
  edit: ["编辑", "文件"],
  read: ["读取", "文件"],
  list: ["浏览", "目录"],
  search: ["搜索", "工作区"],
  web: ["搜索", "网页"],
  browser: ["操作", "浏览器"],
  computer: ["操作", "电脑"],
  agent: ["协调", "子代理"],
  plan: ["更新", "计划"],
  memory: ["查阅", "记忆"],
  image: ["查看", "截图"],
  tool: ["使用", "工具"],
};

const EN_GROUP: Record<ActivityCategory, [string, string]> = {
  command: ["running commands", "ran commands"],
  edit: ["editing files", "edited files"],
  read: ["reading files", "read files"],
  list: ["listing folders", "listed folders"],
  search: ["searching the workspace", "searched the workspace"],
  web: ["searching the web", "searched the web"],
  browser: ["using the browser", "used the browser"],
  computer: ["using the computer", "used the computer"],
  agent: ["coordinating agents", "coordinated agents"],
  plan: ["updating the plan", "updated the plan"],
  memory: ["checking memory", "checked memory"],
  image: ["viewing screenshots", "viewed screenshots"],
  tool: ["using tools", "used tools"],
};

function zhVerb(stem: string, tense: ActivityTense): string {
  switch (tense) {
    case "active": return `正在${stem}`;
    case "failed": return `${stem}失败`;
    case "waiting": return `待确认${stem}`;
    case "denied": return `已拒绝${stem}`;
    case "stopped": return `已中断${stem}`;
    case "skipped": return `未${stem}`;
    case "uncertain": return `${stem}结果待确认`;
    case "refresh": return `需刷新后${stem}`;
    default: return `已${stem}`;
  }
}

function enVerb(verb: EnglishVerb, tense: ActivityTense): string {
  switch (tense) {
    case "active": return verb.ing;
    case "failed": return `Couldn't ${verb.base}`;
    case "waiting": return `Waiting to ${verb.base}`;
    case "denied": return `Not allowed to ${verb.base}`;
    case "stopped": return `Stopped ${verb.ing.charAt(0).toLowerCase()}${verb.ing.slice(1)}`;
    case "skipped": return `Didn't ${verb.base}`;
    case "uncertain": return `${verb.past} (unconfirmed)`;
    case "refresh": return `Refresh needed to ${verb.base}`;
    default: return verb.past;
  }
}

function joinZh(phrases: string[]): string {
  if (phrases.length <= 1) return phrases[0] ?? "";
  if (phrases.length === 2) return `${phrases[0]}并${phrases[1]}`;
  return `${phrases.slice(0, -1).join("、")}并${phrases[phrases.length - 1]}`;
}

function joinEn(phrases: string[]): string {
  const sentence = phrases.length <= 1
    ? phrases[0] ?? ""
    : phrases.length === 2
      ? `${phrases[0]} and ${phrases[1]}`
      : `${phrases.slice(0, -1).join(", ")} and ${phrases[phrases.length - 1]}`;
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function formatDuration(seconds: number): string {
  const safe = Math.max(1, Math.round(seconds));
  if (safe < 60) return `${safe}s`;
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours}h ${restMinutes}m` : `${hours}h`;
}

const ZH: RuntimeCopy = {
  lang: "zh",
  thinking: "正在思考…",
  runPhase: { thinking: "正在思考", next: "正在思考下一步", reply: "正在回复", approval: "等待你确认", commandOutput: "正在分析命令结果", agentProgress: "正在汇总子代理进度", stalled: "模型仍在思考", done: "已完成", failed: "未能完成", stopped: "已停止", limit: "已达到用量上限" },
  runSteps: count => `${count} 步`,
  runWait: seconds => `已等待 ${seconds} 秒`,
  thoughtProcess: "思考过程",
  showReasoning: "展开思考过程",
  hideReasoning: "收起思考过程",
  earlier: "较早过程",
  earlierCount: (count) => `${count} 项`,
  earlierToggleLabel: (open, count) => `${open ? "收起" : "展开"}较早过程，${count} 项`,
  earlierToggleTitle: (open) => (open ? "收起较早的进度与工具记录" : "展开较早的进度与工具记录"),
  noteExpand: "展开全文",
  noteCollapse: "收起",
  processToggleTitle: (open) => (open ? "收起任务过程" : "展开完整任务过程"),
  processSummary: (parts, fallback) => {
    const items: string[] = [];
    if (parts.commands) items.push(`运行 ${parts.commands} 条命令`);
    if (parts.filesEdited) items.push(`编辑 ${parts.filesEdited} 个文件`);
    if (parts.filesRead) items.push(`读取 ${parts.filesRead} 个文件`);
    if (parts.imagesViewed) items.push(`查看 ${parts.imagesViewed} 张图片`);
    if (parts.searches) items.push(`搜索 ${parts.searches} 次`);
    if (parts.tools) items.push(`使用 ${parts.tools} 个工具`);
    return items.length ? items.join(" · ") : `${fallback} 个步骤`;
  },
  diffLabel: (added, removed) => `新增 ${added} 行，删除 ${removed} 行`,
  elapsed: (seconds) => (seconds === null ? "任务过程" : `用时 ${formatDuration(seconds)}`),
  groupTitle: (categories, running) => {
    const phrases = categories.slice(0, 3).map((category, index) => {
      const [verb, object] = ZH_GROUP[category];
      if (!running) return `${verb}了${object}`;
      return index === 0 ? `正在${verb}${object}` : `${verb}${object}`;
    });
    return joinZh(phrases) || (running ? "正在使用工具" : "使用了工具");
  },
  statusLabel: (status) => ({
    not_executed: "未执行",
    uncertain: "结果待确认",
    refresh_required: "需要刷新",
    started: "进行中",
    running: "进行中",
    streaming: "进行中",
    streaming_arguments: "进行中",
    waiting: "等待中",
    waiting_approval: "等待确认",
    pending: "等待中",
    completed: "已完成",
    changed: "已修改",
    failed: "失败",
    denied: "已拒绝",
    cancelled: "已取消",
    interrupted: "已中断",
  } as Record<string, string>)[status] ?? status,
  liveHint: (kind) => ({
    approval: "等待你确认权限…",
    waiting: "已就绪，等待继续…",
    command: "命令正在执行，等待输出…",
    edit: "正在生成文件修改…",
    tool: "正在执行，等待结果…",
  })[kind],
  expandDetails: "展开详情",
  collapseDetails: "收起详情",
  approvalTitle: "需要你的确认",
  approvalTool: "工具",
  deny: "拒绝",
  allow: "允许",
  turnFailed: "这一轮没有完成",
  copy: "复制",
  copied: "已复制",
  copyMessage: "复制消息",
  quoteAnswer: "引用回答",
  quoteMessage: "引用消息",
  quoteAnswerLabel: "引用这段回答",
  quoteMessageLabel: "引用这条消息",
  editMessage: "放到输入框里编辑",
  editMessageLabel: "编辑这条消息",
  helpful: "有帮助",
  notHelpful: "没帮助",
  helpfulLabel: "标记回复有帮助",
  notHelpfulLabel: "标记回复没帮助",
  userActions: "消息操作",
  assistantActions: "回复操作",
  subAgentsRunning: "子代理正在并行工作",
  subAgentsUsed: "本轮使用了子代理",
  subAgentsOpen: "查看工作区",
  subAgentsTitle: "在右侧打开子代理工作区",
  openArtifact: "查看产物",
  artifactUpdates: (path, count) => `${path} · ${count} 次更新`,
  imagesViewed: (count) => `${count} 张图片`,
  imageLoading: "正在载入预览…",
  imageUnavailable: "图片预览不可用",
  imageAlt: (name) => `浏览器截图 ${name}`,
  decisionRetryPrompt: "刚才的选项没有生成完整。请只重新给出完整的选项卡，不要重复前面的分析。",
  guidanceRecap: "本轮补充的要求",
  activityRegion: "任务活动",
  changedFilesTitle: (count, name) => (count === 1 ? `已编辑 ${name}` : `已修改 ${count} 个文件`),
  changedFilesRegion: "本轮改动的文件",
  review: "审查",
  reviewTitle: "在右侧审查面板查看代码改动",
  openInReview: (path) => `在审查中打开 ${path}`,
  preview: "预览",
  previewArtifact: (name) => `预览 ${name}`,
  previewArtifactTitle: (path) => `在侧栏预览 ${path}`,
  turnEnding: (error) => {
    const text = error.trim();
    if (USER_STOP.test(text)) return { tone: "stopped", text: "你停止了这一轮" };
    if (PROCESS_STOP.test(text)) return { tone: "stopped", text: "Loom 在这一轮完成前停止了运行" };
    return { tone: "error", text: text || "这一轮没有完成" };
  },
  verb: (spec, tense) => zhVerb(VERBS[spec].zh, tense),
};

const EN: RuntimeCopy = {
  lang: "en",
  thinking: "Thinking…",
  runPhase: { thinking: "Thinking", next: "Thinking about the next step", reply: "Writing a reply", approval: "Waiting for your approval", commandOutput: "Reading the command output", agentProgress: "Reviewing sub-agent progress", stalled: "The model is still thinking", done: "Done", failed: "Didn't finish", stopped: "Stopped", limit: "Usage limit reached" },
  runSteps: count => plural(count, "step", "steps"),
  runWait: seconds => `${seconds}s`,
  thoughtProcess: "Thought process",
  showReasoning: "Show thought process",
  hideReasoning: "Hide thought process",
  earlier: "Earlier steps",
  earlierCount: (count) => String(count),
  earlierToggleLabel: (open, count) => `${open ? "Hide" : "Show"} ${plural(count, "earlier step", "earlier steps")}`,
  earlierToggleTitle: (open) => (open ? "Hide earlier progress and tool records" : "Show earlier progress and tool records"),
  noteExpand: "Show full note",
  noteCollapse: "Show less",
  processToggleTitle: (open) => (open ? "Collapse the work log" : "Expand the full work log"),
  processSummary: (parts, fallback) => {
    const items: string[] = [];
    if (parts.commands) items.push(`Ran ${plural(parts.commands, "command", "commands")}`);
    if (parts.filesEdited) items.push(`edited ${plural(parts.filesEdited, "file", "files")}`);
    if (parts.filesRead) items.push(`read ${plural(parts.filesRead, "file", "files")}`);
    if (parts.imagesViewed) items.push(`viewed ${plural(parts.imagesViewed, "image", "images")}`);
    if (parts.searches) items.push(`searched ${plural(parts.searches, "time", "times")}`);
    if (parts.tools) items.push(`used ${plural(parts.tools, "tool", "tools")}`);
    if (!items.length) return plural(fallback, "step", "steps");
    const sentence = items.join(" · ");
    return sentence.charAt(0).toUpperCase() + sentence.slice(1);
  },
  diffLabel: (added, removed) => `${added} lines added, ${removed} removed`,
  elapsed: (seconds) => (seconds === null ? "Work log" : `Worked ${formatDuration(seconds)}`),
  groupTitle: (categories, running) => {
    const phrases = categories.slice(0, 3).map((category) => EN_GROUP[category][running ? 0 : 1]);
    return joinEn(phrases) || (running ? "Using tools" : "Used tools");
  },
  statusLabel: (status) => ({
    not_executed: "Not executed",
    uncertain: "Effect unconfirmed",
    refresh_required: "Refresh required",
    started: "Running",
    running: "Running",
    streaming: "Running",
    streaming_arguments: "Running",
    waiting: "Waiting",
    waiting_approval: "Needs approval",
    pending: "Waiting",
    completed: "Completed",
    changed: "Changed",
    failed: "Failed",
    denied: "Denied",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
  } as Record<string, string>)[status] ?? status,
  liveHint: (kind) => ({
    approval: "Waiting for your approval…",
    waiting: "Ready, waiting to continue…",
    command: "Command running, waiting for output…",
    edit: "Preparing the file changes…",
    tool: "Working, waiting for the result…",
  })[kind],
  expandDetails: "Show details",
  collapseDetails: "Hide details",
  approvalTitle: "Needs your approval",
  approvalTool: "Tool",
  deny: "Deny",
  allow: "Allow",
  turnFailed: "This turn didn't finish",
  copy: "Copy",
  copied: "Copied",
  copyMessage: "Copy message",
  quoteAnswer: "Quote reply",
  quoteMessage: "Quote message",
  quoteAnswerLabel: "Quote this reply",
  quoteMessageLabel: "Quote this message",
  editMessage: "Edit in the composer",
  editMessageLabel: "Edit this message",
  helpful: "Helpful",
  notHelpful: "Not helpful",
  helpfulLabel: "Mark reply as helpful",
  notHelpfulLabel: "Mark reply as not helpful",
  userActions: "Message actions",
  assistantActions: "Reply actions",
  subAgentsRunning: "Sub-agents are working in parallel",
  subAgentsUsed: "This turn used sub-agents",
  subAgentsOpen: "Open workspace",
  subAgentsTitle: "Open the sub-agent workspace",
  openArtifact: "Open artifact",
  artifactUpdates: (path, count) => `${path} · ${plural(count, "update", "updates")}`,
  imagesViewed: (count) => plural(count, "image", "images"),
  imageLoading: "Loading preview…",
  imageUnavailable: "Preview unavailable",
  imageAlt: (name) => `Browser screenshot ${name}`,
  decisionRetryPrompt: "The previous options were incomplete. Please send the complete option card again without repeating the earlier analysis.",
  guidanceRecap: "Guidance added during this turn",
  activityRegion: "Task activity",
  changedFilesTitle: (count, name) => (count === 1 ? `Edited ${name}` : `Changed ${plural(count, "file", "files")}`),
  changedFilesRegion: "Changed files",
  review: "Review",
  reviewTitle: "Open the changes in the review panel",
  openInReview: (path) => `Open ${path} in review`,
  preview: "Preview",
  previewArtifact: (name) => `Preview ${name}`,
  previewArtifactTitle: (path) => `Preview ${path} in the side panel`,
  turnEnding: (error) => {
    const text = error.trim();
    if (USER_STOP.test(text)) return { tone: "stopped", text: "You stopped this turn" };
    if (PROCESS_STOP.test(text)) return { tone: "stopped", text: "Loom stopped before this turn finished" };
    return { tone: "error", text: text || "This turn didn't finish" };
  },
  verb: (spec, tense) => enVerb(VERBS[spec].en, tense),
};

export function runtimeCopy(language: LoomLanguage | RuntimeLanguage): RuntimeCopy {
  return language === "en" ? EN : ZH;
}

export function useRuntimeCopy(): RuntimeCopy {
  const language = useLoomLanguage();
  return useMemo(() => runtimeCopy(language), [language]);
}

export function activityTense(status: string): ActivityTense {
  switch (status) {
    case "started":
    case "running":
    case "streaming":
    case "streaming_arguments":
      return "active";
    case "waiting":
    case "waiting_approval":
    case "pending":
      return "waiting";
    case "failed":
      return "failed";
    case "denied":
      return "denied";
    case "cancelled":
    case "interrupted":
      return "stopped";
    case "not_executed":
      return "skipped";
    case "uncertain":
      return "uncertain";
    case "refresh_required":
      return "refresh";
    default:
      return "done";
  }
}

function genericToolLabel(item: TranscriptItem, fallbackLabel: string, lang: RuntimeLanguage): string {
  const raw = String(item.toolName ?? "").trim();
  if (!raw) return fallbackLabel;
  const mcp = raw.match(/^mcp[._]{1,2}([^._]+)[._]{1,2}(.+)$/i);
  if (mcp) return `${mcp[1]} · ${mcp[2].replace(/[_.-]+/g, " ")}`;
  return lang === "zh" ? raw : raw.replace(/[_]+/g, " ");
}

/**
 * Describe one task row. `primary` is the item that carries the outcome (a
 * process or diff once it exists); `wrapper` is the tool call that requested
 * it, which supplies the target while the outcome is still pending.
 */
export function describeActivity(
  primary: TranscriptItem,
  wrapper: TranscriptItem | null,
  status: string,
  copy: RuntimeCopy,
  options: { fallbackToolLabel: string; screenshotCount: number },
): ActivityDescription {
  const tense = activityTense(status);
  const lang = copy.lang;
  const source = wrapper ?? primary;

  if (primary.type === "process" || isCommandTool(source)) {
    const command = commandFromItem(primary.type === "process" ? primary : source) || commandFromItem(source);
    return {
      category: "command",
      verb: copy.verb("run", tense),
      target: command || (lang === "zh" ? "命令" : "a command"),
      title: command || undefined,
      code: Boolean(command),
    };
  }

  if (primary.type === "file_edit" || isEditTool(source)) {
    const paths = primary.type === "file_edit" ? editTargets(primary) : editTargets(source);
    const writes = /^(write_workspace_text|write_file)$/.test(normalizedToolName(source)) && primary.type !== "file_edit";
    return {
      category: "edit",
      verb: copy.verb(writes ? "write" : "edit", tense),
      target: pathsLabel(paths, lang),
      title: paths.join("\n") || undefined,
    };
  }

  if (options.screenshotCount) {
    return {
      category: "image",
      verb: copy.verb("inspect", tense),
      target: copy.imagesViewed(options.screenshotCount),
    };
  }

  const name = normalizedToolName(source);
  const spec = TOOL_SPECS[name];
  if (spec) {
    const target = spec.target(args(source), source, lang);
    return {
      category: spec.category,
      verb: copy.verb(spec.verb, tense),
      target: target || genericToolLabel(source, options.fallbackToolLabel, lang),
      title: target || undefined,
      code: spec.code,
    };
  }

  if (name.startsWith("browser")) {
    return { category: "browser", verb: copy.verb("operate", tense), target: lang === "zh" ? "浏览器" : "the browser" };
  }
  if (name.startsWith("computer")) {
    return { category: "computer", verb: copy.verb("operate", tense), target: lang === "zh" ? "电脑" : "the computer" };
  }

  return {
    category: "tool",
    verb: copy.verb("call", tense),
    target: genericToolLabel(source, options.fallbackToolLabel, lang),
  };
}

export type ThreadStatus =
  | "idle"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "limit_reached";

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

export interface ProjectRecord {
  id: string;
  name: string;
  root: string;
  instructions?: string;
  threadCount: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface ProjectListResult {
  projects: ProjectRecord[];
  unfiledThreadCount: number;
}

/** One file staged for the next message, as the composer knows it. */
export interface Attachment {
  id: string;
  name: string;
  path: string;
  size: number;
  isImage: boolean;
  previewUrl?: string;
}

export interface ThreadRecord {
  id: string;
  /** Empty when no project claims this thread's workspace. */
  projectId?: string;
  title: string;
  /** True only for a committed manual/model title, never the neutral provisional label. */
  customTitle?: boolean;
  /** Server-owned title lifecycle; pending/fallback labels are intentionally non-semantic. */
  titleSource?: "manual" | "auto" | "pending" | "fallback" | string;
  autoTitlePending?: boolean;
  autoTitleFallback?: boolean;
  workspace: string;
  permissionMode: string;
  modelSelection?: string | null;
  model?: string | null;
  modelProvider?: string | null;
  modelBaseUrl?: string | null;
  modelVision?: boolean;
  reasoning?: { kind: string; value: string } | null;
  status: ThreadStatus | string;
  currentTurnId?: string | null;
  createdAt: string;
  updatedAt: string;
  active?: boolean;
  archived?: boolean;
  usage?: Usage;
}

export interface TranscriptItem {
  id: string;
  threadId: string;
  turnId?: string | null;
  type: "user_message" | "assistant_message" | "tool_call" | "process" | "file_edit" | "approval" | "error" | string;
  status?: string;
  text?: string;
  /** Provider-exposed reasoning text/summary, separate from the assistant answer. */
  reasoning?: string;
  /** Runtime origin for user messages, e.g. "user" or same-turn "steering". */
  source?: string;
  /** Stable client id for same-turn steering input. */
  inputId?: string;
  /** Original user submission time; steering may be consumed later at a safe boundary. */
  submittedAt?: string;
  /** Runtime-authored assistant phase. Never infer finality from transcript order. */
  phase?: "commentary" | "final_answer" | string;
  /** True when Loom supplied a safe progress preamble for a tool-only model response. */
  runtimeAuthored?: boolean;
  /** Stable model-step identity used to correlate the terminal answer. */
  stepId?: string | null;
  toolName?: string;
  callId?: string;
  arguments?: unknown;
  content?: string;
  stdout?: string;
  stderr?: string;
  diff?: string;
  paths?: string[];
  reason?: string;
  error?: string;
  effect?: string;
  updatedAt?: string;
  createdAt?: string;
  [key: string]: unknown;
}

export type ApprovalDecision = "accept" | "decline";

export interface PendingApproval {
  /** Durable correlation id. Null means display-only legacy state; responses fail closed. */
  requestId: string | null;
  threadId: string;
  turnId: string | null;
  /** The tool item under review, matching Codex approval itemId semantics. */
  itemId: string;
  /** Loom-only transcript affordance; not the runtime item being approved. */
  approvalItemId: string;
  callId: string;
  requestType: "toolExecution" | string;
  /** Loom lifecycle stage extension; distinct from Codex command-approval `kind`. */
  approvalStage: string;
  /** Loom lifecycle extension; Codex v2 command approval has no retryReason field. */
  retryReason: string | null;
  startedAtMs: number;
  toolName: string;
  arguments: unknown;
  effect: string;
  reason: string;
  permissionMode: string | null;
  networkApprovalContext: Record<string, unknown> | null;
  availableDecisions: ApprovalDecision[];
}

export interface TurnRecord {
  id: string;
  threadId: string;
  status: string;
  items: TranscriptItem[];
  usage?: Usage;
  startedAt?: string | null;
  completedAt?: string | null;
  finalText?: string;
  /** Authoritative model step that completed the turn. */
  finalStepId?: string | null;
  /** Authoritative transcript item for the final user-visible answer. */
  finalItemId?: string | null;
}

export interface ThreadReadResult {
  thread: ThreadRecord;
  turns: TurnRecord[];
  pendingApproval?: PendingApproval | null;
  finalText?: string;
  error?: string;
}

export type ContextSegmentKey = "conversation" | "toolSchemas" | "free";

export interface ContextSegment {
  key: ContextSegmentKey;
  tokens: number;
}

/** What the model had to give up to make the last request fit. */
export interface ContextPressure {
  schemaMode: string;
  toolsOmitted: string[];
  toolOutputsReduced: number;
  toolOutputsCollapsed: number;
  userMessagesTruncated: number;
  /** Tool results were collapsed: the agent can no longer read its own output. */
  blinded: boolean;
}

export interface ContextReport {
  threadId?: string;
  windowTokens: number | null;
  effectiveWindowTokens: number;
  inputBudgetTokens: number;
  outputReserveTokens: number;
  autoCompactTokens: number;
  toolOutputTokenLimit: number;
  windowKnown: boolean;
  limitsSource: string;
  usedTokens: number;
  usedPercent: number;
  freeTokens: number;
  accounting: string;
  messageCount: number;
  segments: ContextSegment[];
  pressure: ContextPressure;
  compactions: number;
  lastCompactedAt: string;
  measuredAt: string;
  /** True after compaction until the next real model request replaces the estimate. */
  measurementPending?: boolean;
}

export interface ContextCompactionProgress {
  threadId: string;
  operationId: string;
  status: "started" | "running" | "completed" | "failed";
  stage: "queued" | "preparing" | "summarizing" | "completed" | "failed" | string;
  message: string;
  error?: string;
  startedAt: string;
  updatedAt: string;
}

export interface ModelReasoningOption {
  value: string;
  label: string;
  description: string;
  advanced: boolean;
}

export interface ModelReasoningState {
  kind: "openai-effort" | "minimax-thinking" | string;
  value: string;
  defaultValue: string;
  options: ModelReasoningOption[];
  source: string;
}

export interface ModelProfile {
  selection: string;
  id: string;
  kind: "builtin" | "saved";
  name: string;
  adapter: string;
  baseUrl: string;
  model: string;
  groupId?: string;
  groupName?: string;
  groupOrder?: number;
  family?: string;
  protocol?: string;
  configured?: boolean;
  setupOnly?: boolean;
  statusMessage?: string;
  vision?: boolean;
  reasoning?: ModelReasoningState | null;
}

export interface CurrentModel extends ModelProfile {
  provider: string;
}

export interface ModelSnapshot {
  primary: ModelProfile;
  profiles: ModelProfile[];
  activeModelId: string | null;
  current: CurrentModel | null;
  recentModels: string[];
}

export interface AddModelInput {
  name: string;
  adapter: "openai" | "openai-compatible";
  baseUrl: string;
  model: string;
  apiKey: string;
  vision?: boolean;
}

export interface EditModelInput extends AddModelInput {
  selection: string;
}

export interface ModelTestResult {
  ok: boolean;
  selection: string;
  status: number;
  latencyMs: number;
  endpoint: string;
  model: string;
  modelListed: boolean;
  discoveredModels: number;
  capabilities: {
    chat: boolean;
    streaming: boolean;
    vision: boolean;
    reasoning: boolean;
  };
}

export interface StickerPreferences {
  schema?: "ai_ledger_chat_expression_preferences_v2" | string;
  frequency: number;
  intensity: number;
  maxPerReply: number;
  repeatCount: number;
}

export interface LoomAppearanceSettings {
  scale?: "90" | "100" | "110" | "120" | "130";
  density?: "compact" | "comfortable" | "spacious";
  reducedMotion?: boolean;
  conversationWidth?: "focused" | "balanced" | "wide";
  sidebarWidth?: "compact" | "standard" | "wide";
  inspectorWidth?: "compact" | "standard" | "wide";
  chatFontSize?: number;
  messageLineHeight?: "compact" | "comfortable" | "relaxed";
  ambientEffects?: boolean;
  codeFont?: string;
  codeFontSize?: number;
  codeLineHeight?: "compact" | "comfortable" | "relaxed";
  codeWrap?: boolean;
}

export interface LoomShortcutSettings {
  newConversation?: string;
  searchConversations?: string;
  openSettings?: string;
  focusComposer?: string;
  toggleSidebar?: string;
  toggleInspector?: string;
  attachFiles?: string;
  stopTask?: string;
}

export interface LoomTerminalSettings {
  shell?: "powershell" | "cmd" | "git-bash" | "wsl";
  encoding?: "utf-8" | "system";
  commandTimeoutSeconds?: number;
  preserveBackgroundProcesses?: boolean;
}

export interface LoomBrowserSettings {
  mode?: "auto" | "local-launch" | "cdp-attach" | "extension";
  cdpUrl?: string;
  preferredEngine?: "edge" | "chrome" | "system";
  persistSessions?: boolean;
  modelSelectsConnection?: boolean;
  allowPrivateNetworks?: boolean;
}

export interface LoomComputerSettings {
  verifyActions?: boolean;
  screenshotQuality?: "fast" | "balanced" | "high";
}

export interface LoomPrivacySettings {
  telemetry?: boolean;
  crashReports?: boolean;
}

export interface LoomSettings {
  schemaVersion: number;
  capabilities: {
    computerUse?: boolean;
    browserUse?: boolean;
    webSearch?: boolean;
    mcp?: boolean;
    skills?: boolean;
    toolSearch?: boolean;
    codeMode?: boolean;
    processRuntime?: boolean;
    attachments?: boolean;
    stickers?: boolean;
    [key: string]: boolean | undefined;
  };
  appearance?: LoomAppearanceSettings;
  shortcuts?: LoomShortcutSettings;
  terminal?: LoomTerminalSettings;
  browser?: LoomBrowserSettings;
  computer?: LoomComputerSettings;
  privacy?: LoomPrivacySettings;
}

export interface ModelRestartResult {
  initialization: InitializeResult;
  models: ModelSnapshot;
  hotSwitch?: boolean;
  thread?: ThreadRecord;
}

export interface ReasoningUpdateResult {
  runtime: InitializeResult["runtime"];
  models: ModelSnapshot;
  thread?: ThreadRecord;
}

export interface InitializeResult {
  protocolVersion: number;
  serverInfo: { name: string; version: string };
  capabilities: Record<string, unknown>;
  runtime: {
    model?: string;
    defaultWorkspace?: string;
    defaultPermissionMode?: string;
    permissionModes?: string[];
    reasoning?: { kind: string; value: string } | null;
    stickerPreferences?: StickerPreferences | null;
    settings?: LoomSettings;
    capabilityStatus?: Record<string, Record<string, unknown>>;
    registeredToolCount?: number;
    exposedToolCount?: number;
    attachments?: {
      images?: boolean;
      files?: boolean;
      maxCount?: number;
      maxImageBytes?: number;
      maxFileBytes?: number;
    };
    activeThreadIds?: string[];
    taskErrors?: Record<string, string>;
  };
}

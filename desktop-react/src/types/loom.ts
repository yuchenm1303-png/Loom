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
  workspace: string;
  permissionMode: string;
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

export interface TurnRecord {
  id: string;
  threadId: string;
  status: string;
  items: TranscriptItem[];
  usage?: Usage;
  startedAt?: string | null;
  completedAt?: string | null;
}

export interface ThreadReadResult {
  thread: ThreadRecord;
  turns: TurnRecord[];
  pendingApproval?: Record<string, unknown> | null;
  finalText?: string;
  error?: string;
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
  mode?: "local-launch" | "cdp-attach" | "extension";
  cdpUrl?: string;
  preferredEngine?: "edge" | "chrome" | "system";
  persistSessions?: boolean;
  modelSelectsConnection?: boolean;
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
}

export interface ReasoningUpdateResult {
  runtime: InitializeResult["runtime"];
  models: ModelSnapshot;
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

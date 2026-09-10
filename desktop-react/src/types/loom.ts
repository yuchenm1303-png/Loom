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
}

export interface StickerPreferences {
  schema?: "ai_ledger_chat_expression_preferences_v2" | string;
  frequency: number;
  intensity: number;
  maxPerReply: number;
  repeatCount: number;
}

export interface ModelRestartResult {
  initialization: InitializeResult;
  models: ModelSnapshot;
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
  };
}

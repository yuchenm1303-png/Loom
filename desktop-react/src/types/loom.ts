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

export interface ThreadRecord {
  id: string;
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

export interface InitializeResult {
  protocolVersion: number;
  serverInfo: { name: string; version: string };
  capabilities: Record<string, unknown>;
  runtime: {
    model?: string;
    defaultWorkspace?: string;
    defaultPermissionMode?: string;
    permissionModes?: string[];
  };
}

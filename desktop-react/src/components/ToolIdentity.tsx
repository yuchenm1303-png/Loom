import type { TranscriptItem } from "../types/loom";
import {
  AnthropicGlyph,
  ApprovalGlyph,
  AutomationGlyph,
  BrowserGlyph,
  CalculatorGlyph,
  ComputerGlyph,
  DeepSeekGlyph,
  FileEditGlyph,
  GeminiGlyph,
  GenericToolGlyph,
  GitHubGlyph,
  MCPGlyph,
  MemoryGlyph,
  MiniMaxGlyph,
  OpenAIGlyph,
  SearchGlyph,
  SentinelXGlyph,
  SkillGlyph,
  SubAgentGlyph,
  TerminalGlyph,
  WorkspaceGlyph,
  XAIGlyph,
  type ToolGlyphComponent,
} from "./ToolIconLibrary";

export type ActivityIdentityKind = "brand" | "capability" | "neutral";

export type ActivityFamily =
  | "terminal"
  | "file"
  | "github"
  | "openai"
  | "anthropic"
  | "gemini"
  | "deepseek"
  | "minimax"
  | "xai"
  | "sentinelx"
  | "browser"
  | "computer"
  | "workspace"
  | "search"
  | "memory"
  | "skills"
  | "calculator"
  | "approval"
  | "subagent"
  | "automation"
  | "mcp"
  | "generic";

export interface ActivityIdentity {
  family: ActivityFamily;
  kind: ActivityIdentityKind;
  label: string;
  icon: ToolGlyphComponent;
}

const IDENTITIES: Record<ActivityFamily, ActivityIdentity> = {
  terminal: { family: "terminal", kind: "capability", label: "终端", icon: TerminalGlyph },
  file: { family: "file", kind: "capability", label: "文件编辑", icon: FileEditGlyph },
  github: { family: "github", kind: "brand", label: "GitHub", icon: GitHubGlyph },
  openai: { family: "openai", kind: "brand", label: "OpenAI", icon: OpenAIGlyph },
  anthropic: { family: "anthropic", kind: "brand", label: "Anthropic", icon: AnthropicGlyph },
  gemini: { family: "gemini", kind: "brand", label: "Gemini", icon: GeminiGlyph },
  deepseek: { family: "deepseek", kind: "brand", label: "DeepSeek", icon: DeepSeekGlyph },
  minimax: { family: "minimax", kind: "brand", label: "MiniMax", icon: MiniMaxGlyph },
  xai: { family: "xai", kind: "brand", label: "xAI / Grok", icon: XAIGlyph },
  sentinelx: { family: "sentinelx", kind: "capability", label: "SentinelX", icon: SentinelXGlyph },
  browser: { family: "browser", kind: "capability", label: "浏览器", icon: BrowserGlyph },
  computer: { family: "computer", kind: "capability", label: "电脑操作", icon: ComputerGlyph },
  workspace: { family: "workspace", kind: "capability", label: "工作区", icon: WorkspaceGlyph },
  search: { family: "search", kind: "capability", label: "联网搜索", icon: SearchGlyph },
  memory: { family: "memory", kind: "capability", label: "Memory", icon: MemoryGlyph },
  skills: { family: "skills", kind: "capability", label: "Skill", icon: SkillGlyph },
  calculator: { family: "calculator", kind: "capability", label: "计算器", icon: CalculatorGlyph },
  approval: { family: "approval", kind: "capability", label: "权限审批", icon: ApprovalGlyph },
  subagent: { family: "subagent", kind: "capability", label: "子代理", icon: SubAgentGlyph },
  automation: { family: "automation", kind: "capability", label: "自动化", icon: AutomationGlyph },
  mcp: { family: "mcp", kind: "capability", label: "MCP", icon: MCPGlyph },
  generic: { family: "generic", kind: "neutral", label: "工具", icon: GenericToolGlyph },
};

function stringHint(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function itemHint(item: TranscriptItem): string {
  const metadata = typeof item.metadata === "object" && item.metadata !== null
    ? item.metadata as Record<string, unknown>
    : null;
  const result = typeof item.result === "object" && item.result !== null
    ? item.result as Record<string, unknown>
    : null;
  const fields: unknown[] = [
    item.toolName,
    item.source,
    item.provider,
    item.providerName,
    item.connector,
    item.connectorName,
    item.server,
    item.serverName,
    item.mcpServer,
    metadata?.provider,
    metadata?.provider_name,
    metadata?.connector,
    metadata?.connector_name,
    metadata?.server,
    metadata?.server_name,
    metadata?.mcp_server,
    result?.provider,
    result?.provider_name,
    result?.connector,
    result?.connector_name,
    result?.server,
    result?.server_name,
    result?.mcp_server,
  ];
  return fields.map(stringHint).filter(Boolean).join(" ");
}

function normalizedToolName(item: TranscriptItem): string {
  return stringHint(item.toolName);
}

function brandIdentity(hint: string): ActivityIdentity | null {
  // Specific external brands are matched before generic capability heuristics.
  // Word-ish boundaries keep innocent tool names such as "githubish" or
  // "openair" from accidentally inheriting a vendor mark.
  if (/(^|[^a-z0-9])github([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.github;
  if (/sentinel[\s_.:\/-]*x/i.test(hint)) return IDENTITIES.sentinelx;
  if (/(^|[^a-z0-9])openai([^a-z0-9]|$)|(^|[^a-z0-9])chatgpt([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.openai;
  if (/(^|[^a-z0-9])anthropic([^a-z0-9]|$)|(^|[^a-z0-9])claude([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.anthropic;
  if (/(^|[^a-z0-9])gemini([^a-z0-9]|$)|(^|[^a-z0-9])google[\s_.:\/-]*gemini([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.gemini;
  if (/(^|[^a-z0-9])deepseek([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.deepseek;
  if (/mini[\s_.:\/-]*max/i.test(hint)) return IDENTITIES.minimax;
  if (/(^|[^a-z0-9])xai([^a-z0-9]|$)|(^|[^a-z0-9])grok([^a-z0-9]|$)/i.test(hint)) return IDENTITIES.xai;
  return null;
}

/**
 * Resolve a stable visual identity for runtime activity.
 *
 * Provider/server metadata wins when available, while canonical MCP names keep
 * old transcript history compatible. Capability marks are deliberately Loom
 * owned; only real external providers use brand marks. Unknown tools end at a
 * neutral custom glyph instead of borrowing an unrelated brand or wrench.
 */
export function activityIdentity(item: TranscriptItem): ActivityIdentity {
  if (item.type === "process") return IDENTITIES.terminal;
  if (item.type === "file_edit") return IDENTITIES.file;

  const name = normalizedToolName(item);
  const hint = itemHint(item);
  const brand = brandIdentity(hint);
  if (brand) return brand;

  if (
    name === "computer"
    || name === "computer_action"
    || name.startsWith("computer_")
    || name.startsWith("computer.")
  ) return IDENTITIES.computer;

  if (
    name === "browser"
    || name.startsWith("browser_")
    || name.startsWith("browser.")
    || name.includes("playwright")
  ) return IDENTITIES.browser;

  if (
    name === "approval"
    || name.includes("approval")
    || name.includes("permission")
    || name.startsWith("allow_")
    || name.startsWith("deny_")
  ) return IDENTITIES.approval;

  if (
    name === "spawn_agent"
    || name === "subagent"
    || name === "sub_agent"
    || name.includes("sub_agent")
    || name.includes("subagent")
    || name.includes("delegate_agent")
  ) return IDENTITIES.subagent;

  if (
    name.includes("automation")
    || name.includes("workflow")
    || name.includes("schedule")
    || name.includes("trigger")
  ) return IDENTITIES.automation;

  if (
    name.includes("workspace")
    || /^(read|write|edit|list|search)[_.-](file|files|directory|directories)$/.test(name)
    || /^(read|write|edit)[_.-].*file/.test(name)
  ) return IDENTITIES.workspace;

  if (
    name === "web_search"
    || name === "search_web"
    || name.startsWith("web_search_")
    || name.startsWith("web.search")
  ) return IDENTITIES.search;

  if (name.includes("memory")) return IDENTITIES.memory;
  if (name.includes("skill")) return IDENTITIES.skills;
  if (name === "calculator" || name.startsWith("calculator_") || name.startsWith("calculate_")) return IDENTITIES.calculator;

  if (
    name === "mcp"
    || name.startsWith("mcp__")
    || name.startsWith("mcp_")
    || name.startsWith("mcp.")
    || /(^|\s)mcp([\s_.:\/-]|$)/i.test(hint)
  ) return IDENTITIES.mcp;

  return IDENTITIES.generic;
}

export function activityGroupIdentity(items: TranscriptItem[]): ActivityIdentity {
  if (!items.length) return IDENTITIES.generic;
  const identities = items.map(activityIdentity);
  const first = identities[0];
  return identities.every((identity) => identity.family === first.family)
    ? first
    : IDENTITIES.generic;
}

function IdentityGlyph({ identity, size }: { identity: ActivityIdentity; size: number }) {
  const Icon = identity.icon;
  return (
    <span
      className={`tool-identity-glyph is-${identity.kind}`}
      data-tool-family={identity.family}
      data-tool-kind={identity.kind}
      aria-hidden="true"
    >
      <Icon size={size} />
    </span>
  );
}

export function ActivityGlyph({ item, size = 13 }: { item: TranscriptItem; size?: number }) {
  return <IdentityGlyph identity={activityIdentity(item)} size={size} />;
}

export function ActivityGroupGlyph({ items, size = 14 }: { items: TranscriptItem[]; size?: number }) {
  return <IdentityGlyph identity={activityGroupIdentity(items)} size={size} />;
}

function humanizeRemoteTool(name: string): string {
  const clean = name.replace(/[_.-]+/g, " ").trim();
  if (!clean) return name;
  return clean.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function activityToolLabel(item: TranscriptItem): string {
  const raw = String(item.toolName ?? "").trim();
  const name = raw.toLowerCase();
  const identity = activityIdentity(item);

  if (!raw) return identity.label;
  if (name === "computer_action") return "电脑操作";
  if (name === "web_search") return "联网搜索";
  if (name === "calculator") return "计算器";
  if (name === "spawn_agent") return "启动子代理";

  const canonicalMcp = raw.match(/^mcp[._]{1,2}([^._]+)[._]{1,2}(.+)$/i);
  if (canonicalMcp && identity.kind === "brand") {
    return `${identity.label} · ${humanizeRemoteTool(canonicalMcp[2])}`;
  }

  return raw;
}

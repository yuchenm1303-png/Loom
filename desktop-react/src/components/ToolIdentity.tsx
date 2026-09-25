import {
  Calculator,
  Database,
  FileDiff,
  FolderOpen,
  Github,
  Globe2,
  Monitor,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { TranscriptItem } from "../types/loom";

export type ActivityFamily =
  | "terminal"
  | "file"
  | "github"
  | "sentinelx"
  | "browser"
  | "computer"
  | "workspace"
  | "search"
  | "memory"
  | "skills"
  | "calculator"
  | "mcp"
  | "generic";

export interface ActivityIdentity {
  family: ActivityFamily;
  label: string;
  icon: LucideIcon;
}

const IDENTITIES: Record<ActivityFamily, ActivityIdentity> = {
  terminal: { family: "terminal", label: "终端", icon: Terminal },
  file: { family: "file", label: "文件", icon: FileDiff },
  github: { family: "github", label: "GitHub", icon: Github },
  sentinelx: { family: "sentinelx", label: "SentinelX", icon: ShieldCheck },
  browser: { family: "browser", label: "浏览器", icon: Globe2 },
  computer: { family: "computer", label: "电脑操作", icon: Monitor },
  workspace: { family: "workspace", label: "工作区", icon: FolderOpen },
  search: { family: "search", label: "联网搜索", icon: Search },
  memory: { family: "memory", label: "Memory", icon: Database },
  skills: { family: "skills", label: "Skill", icon: Sparkles },
  calculator: { family: "calculator", label: "计算器", icon: Calculator },
  mcp: { family: "mcp", label: "MCP", icon: Server },
  generic: { family: "generic", label: "工具", icon: Wrench },
};

function stringHint(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function itemHint(item: TranscriptItem): string {
  const metadata = typeof item.metadata === "object" && item.metadata !== null
    ? item.metadata as Record<string, unknown>
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
  ];
  return fields.map(stringHint).filter(Boolean).join(" ");
}

function normalizedToolName(item: TranscriptItem): string {
  return stringHint(item.toolName);
}

/**
 * Resolve a stable visual identity for runtime activity.
 *
 * The resolver deliberately prefers explicit provider/server hints when the
 * runtime supplies them, but remains backward compatible with existing
 * transcript history where only toolName was persisted. Unknown tools fall
 * back to the neutral wrench instead of guessing a brand.
 */
export function activityIdentity(item: TranscriptItem): ActivityIdentity {
  if (item.type === "process") return IDENTITIES.terminal;
  if (item.type === "file_edit") return IDENTITIES.file;

  const name = normalizedToolName(item);
  const hint = itemHint(item);

  if (/(^|[^a-z])github([^a-z]|$)/i.test(hint)) return IDENTITIES.github;
  if (/sentinel[\s_.:\/-]*x/i.test(hint)) return IDENTITIES.sentinelx;

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
  if (name === "calculator" || name.startsWith("calculator_")) return IDENTITIES.calculator;

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

export function ActivityGlyph({ item, size = 13 }: { item: TranscriptItem; size?: number }) {
  const identity = activityIdentity(item);
  const Icon = identity.icon;
  return <Icon size={size} aria-hidden="true" />;
}

export function ActivityGroupGlyph({ items, size = 14 }: { items: TranscriptItem[]; size?: number }) {
  const identity = activityGroupIdentity(items);
  const Icon = identity.icon;
  return <Icon size={size} aria-hidden="true" />;
}

export function activityToolLabel(item: TranscriptItem): string {
  const raw = String(item.toolName ?? "").trim();
  const name = raw.toLowerCase();

  if (!raw) return "Tool";
  if (name === "computer_action") return "电脑操作";
  if (name === "web_search") return "联网搜索";
  if (name === "calculator") return "计算器";

  return raw;
}

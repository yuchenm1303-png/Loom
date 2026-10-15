import { useRef, useState, type ReactNode, type KeyboardEvent, type DragEvent } from "react";
import {
  FileDiff, FolderOpen, GitFork, Globe2, PanelsTopLeft, PanelRightClose, PanelRightOpen,
  Pulse, Settings, SidebarTriangle, Terminal, UserRound,
} from "./icons";
import { useI18n } from "../i18n";
import "./right-tool-dock.css";

type DockId =
  | "sidebar" | "files" | "browser" | "background" | "agents" | "review"
  | "artifacts" | "inspector" | "profile" | "account" | "settings";

const ORDER_KEY = "loom.ui.rightToolDock.order.v1";
export const DEFAULT_TOOL_ORDER: readonly DockId[] = [
  "sidebar", "files", "browser", "background", "agents", "review",
  "artifacts", "inspector", "profile", "account", "settings",
];

function readOrder(): DockId[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(ORDER_KEY) || "null");
    if (Array.isArray(saved)) {
      const valid = saved.filter((id): id is DockId =>
        typeof id === "string" && DEFAULT_TOOL_ORDER.includes(id as DockId));
      return [...new Set(valid), ...DEFAULT_TOOL_ORDER.filter(id => !valid.includes(id))];
    }
  } catch { /* The dock remains usable when storage is disabled. */ }
  return [...DEFAULT_TOOL_ORDER];
}

export interface RightToolDockProps {
  hidden?: boolean;
  sidebarOpen: boolean;
  filesAvailable: boolean;
  browserAvailable: boolean;
  backgroundOpen: boolean;
  backgroundRunningCount: number;
  agentsOpen: boolean;
  agentCount: number;
  reviewOpen: boolean;
  reviewCount: number;
  artifactsOpen: boolean;
  artifactCount: number;
  inspectorOpen: boolean;
  accountAuthenticated: boolean;
  onToggleSidebar(): void;
  onOpenFiles(): void;
  onToggleBrowser(): void;
  onToggleBackground(): void;
  onToggleAgents(): void;
  onToggleReview(): void;
  onToggleArtifacts(): void;
  onToggleInspector(): void;
  onOpenProfile(): void;
  onOpenAccount(): void;
  onOpenSettings(): void;
}

export function RightToolDock(props: RightToolDockProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [order, setOrder] = useState(readOrder);
  const [dragOver, setDragOver] = useState<DockId | null>(null);
  const dragged = useRef<DockId | null>(null);

  const move = (from: DockId, to: DockId) => {
    if (from === to) return;
    setOrder(previous => {
      const result = previous.filter(id => id !== from);
      const targetIndex = result.indexOf(to);
      result.splice(targetIndex < 0 ? result.length : targetIndex, 0, from);
      try { localStorage.setItem(ORDER_KEY, JSON.stringify(result)); }
      catch { /* Order still works until the next reload. */ }
      return result;
    });
  };

  type Item = { label: string; icon: ReactNode; active?: boolean; disabled?: boolean; reason?: string;
    count?: number; status?: boolean; onClick(): void };
  const items: Record<DockId, Item> = {
    sidebar: {
      label: props.sidebarOpen ? (zh ? "收起侧栏" : "Hide sidebar") : (zh ? "展开侧栏" : "Show sidebar"),
      icon: <SidebarTriangle size={17} fill="currentColor" stroke="none" />, active: props.sidebarOpen,
      onClick: props.onToggleSidebar,
    },
    files: {
      label: zh ? "项目文件" : "Project files", icon: <FolderOpen size={18} strokeWidth={1.65} />,
      disabled: !props.filesAvailable,
      reason: zh ? "先选择一个项目" : "Select a project first",
      onClick: props.onOpenFiles,
    },
    browser: {
      label: zh ? "内置浏览器" : "Browser", icon: <Globe2 size={18} strokeWidth={1.75} />,
      active: props.browserAvailable && undefined, // overridden by browserOpen below
      disabled: !props.browserAvailable,
      reason: zh ? "仅桌面端支持内置浏览器" : "Available in Loom Desktop",
      onClick: props.onToggleBrowser,
    },
    background: {
      label: zh ? "后台任务" : "Background tasks", icon: <Terminal size={18} strokeWidth={1.75} />,
      active: props.backgroundOpen, count: props.backgroundRunningCount,
      status: props.backgroundRunningCount > 0,
      onClick: props.onToggleBackground,
    },
    agents: {
      label: zh ? "子代理" : "Sub-agents", icon: <GitFork size={18} strokeWidth={1.65} />,
      active: props.agentsOpen, count: props.agentCount, onClick: props.onToggleAgents,
    },
    review: {
      label: zh ? "代码审查" : "Review", icon: <FileDiff size={18} strokeWidth={1.7} />,
      active: props.reviewOpen, count: props.reviewCount, onClick: props.onToggleReview,
    },
    artifacts: {
      label: zh ? "产物与预览" : "Artifacts & preview", icon: <PanelsTopLeft size={18} strokeWidth={1.7} />,
      active: props.artifactsOpen, count: props.artifactCount, onClick: props.onToggleArtifacts,
    },
    inspector: {
      label: zh ? "运行详情" : "Runtime inspector",
      icon: props.inspectorOpen ? <PanelRightClose size={18} strokeWidth={1.7} />
        : <PanelRightOpen size={18} strokeWidth={1.7} />,
      active: props.inspectorOpen, onClick: props.onToggleInspector,
    },
    profile: {
      label: zh ? "个人主页与使用洞察" : "Profile & insights",
      icon: <Pulse size={18} strokeWidth={1.7} />, onClick: props.onOpenProfile,
    },
    account: {
      label: zh ? "Loom 账号" : "Account",
      icon: <UserRound size={18} strokeWidth={1.7} />,
      status: props.accountAuthenticated, onClick: props.onOpenAccount,
    },
    settings: {
      label: zh ? "设置" : "Settings", icon: <Settings size={18} strokeWidth={1.7} />,
      onClick: props.onOpenSettings,
    },
  };
  items.browser.active = props.browserOpen;

  const handleDrop = (event: DragEvent<HTMLButtonElement>, id: DockId) => {
    event.preventDefault();
    const source = dragged.current;
    dragged.current = null;
    setDragOver(null);
    if (source) move(source, id);
  };

  const handleKey = (event: KeyboardEvent<HTMLButtonElement>, id: DockId) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    const index = order.indexOf(id);
    const next = index + (event.key === "ArrowUp" ? -1 : 1);
    if (next < 0 || next >= order.length) return;
    event.preventDefault();
    if (event.altKey) move(id, order[next]);
    const target = event.currentTarget.closest(".loom-tool-dock")?.querySelector<HTMLButtonElement>(
      '[data-tool-id="' + (event.altKey ? id : order[next]) + '"]',
    );
    target?.focus();
  };

  if (props.hidden) return null;

  return (
    <aside className="loom-tool-rail" aria-label={zh ? "快捷工具" : "Quick tools"}>
      <nav className="loom-tool-dock" aria-label={zh ? "快捷工具（拖动排序）" : "Quick tools (drag to reorder)"}>
        {order.map((id, index) => {
          const item = items[id];
          return (
            <div className="loom-tool-position" key={id}>
              {(index === 1 || index === 8) && <span className="loom-tool-divider" aria-hidden="true" />}
              <button
                type="button"
                className={"loom-tool-button" + (item.active ? " is-active" : "")
                  + (dragOver === id ? " is-drop-target" : "")}
                data-tool-id={id}
                aria-label={item.label}
                aria-pressed={typeof item.active === "boolean" ? item.active : undefined}
                title={item.disabled ? item.reason : item.label}
                disabled={item.disabled}
                draggable={!item.disabled}
                onClick={item.onClick}
                onKeyDown={event => handleKey(event, id)}
                onDragStart={event => {
                  dragged.current = id;
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", id);
                }}
                onDragOver={event => { event.preventDefault(); setDragOver(id); }}
                onDrop={event => handleDrop(event, id)}
                onDragEnd={() => { dragged.current = null; setDragOver(null); }}
              >
                {item.icon}
                {item.count ? <span className="loom-tool-count" aria-hidden="true">
                  {item.count > 99 ? "99+" : item.count}
                </span> : null}
                {item.status && !item.count ? <span className="loom-tool-status" aria-hidden="true" /> : null}
                <span className="loom-tool-tooltip" aria-hidden="true">{item.disabled ? item.reason : item.label}</span>
              </button>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

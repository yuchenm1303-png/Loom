import {
  useEffect, useLayoutEffect, useRef, useState,
  type CSSProperties, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode,
} from "react";
import { flushSync } from "react-dom";
import {
  FileDiff, FolderOpen, GitFork, Globe2, ListChecks, PanelsTopLeft, PanelRightClose, PanelRightOpen,
  MessagesSquare, Pulse, Settings, Terminal, UserRound, Clock3,
} from "./icons";
import { useI18n } from "../i18n";
import { normalizeShortcut } from "../keyboardShortcuts";
import { DUR, RETAIN, SPRING } from "../motion/tokens";
import { useMotionPresence } from "../motion/useMotionPresence";
import { useReducedMotion } from "../motion/useReducedMotion";
import "./right-tool-dock.css";

type DockId =
  | "files" | "browser" | "terminal" | "background" | "tasks" | "runs" | "agents" | "review"
  | "artifacts" | "inspector" | "profile" | "account" | "settings";

const ORDER_KEY = "loom.ui.rightToolDock.order.v1";
export const DEFAULT_TOOL_ORDER: readonly DockId[] = [
  "files", "browser", "review", "artifacts", "runs", "terminal", "tasks", "background", "agents", "inspector", "profile", "account", "settings",
];
const toolGroup = (id: DockId) => ["profile", "account", "settings"].includes(id) ? 2 : ["runs", "terminal", "tasks", "background", "agents", "inspector"].includes(id) ? 1 : 0;
/** Pointer travel before a press becomes a drag, so a click with a shaky hand is still a click. */
const DRAG_THRESHOLD = 4;
/** How long a lifted tool takes to settle into its slot, in step with `--dock-settle` in the stylesheet. */
const SETTLE_MS = DUR.flow;
/** A tooltip waits for a pointer that rests; one that is already open follows it at once. */
const TIP_DELAY = 360;
const TIP_WARM = 320;

function readOrder(): DockId[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(ORDER_KEY) || "null");
    if (Array.isArray(saved)) {
      const valid = saved.filter((id): id is DockId =>
        typeof id === "string" && DEFAULT_TOOL_ORDER.includes(id as DockId));
      // A new tool lands beside its default neighbours, not after Settings, so an
      // order saved before it existed still reads as one grouped dock.
      const merged = [...new Set(valid)];
      DEFAULT_TOOL_ORDER.forEach(id => {
        if (merged.includes(id)) return;
        const rank = DEFAULT_TOOL_ORDER.indexOf(id);
        const next = merged.findIndex(existing => DEFAULT_TOOL_ORDER.indexOf(existing) > rank);
        merged.splice(next < 0 ? merged.length : next, 0, id);
      });
      return merged.sort((a, b) => toolGroup(a) - toolGroup(b));
    }
  } catch { /* The dock remains usable when storage is disabled. */ }
  return [...DEFAULT_TOOL_ORDER];
}

function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** "Ctrl+Shift+I" -> ["Ctrl", "Shift", "I"]; a bound `+` or `,` stays one key. */
function shortcutKeys(value: string | undefined): string[] {
  const normalized = value ? normalizeShortcut(value) : "";
  return normalized ? normalized.split(/\+(?=.)/) : [];
}

export interface RightToolDockProps {
  hidden?: boolean;
  loading?: boolean;
  hasWorkspace?: boolean;
  filesAvailable: boolean;
  browserAvailable: boolean;
  browserOpen: boolean;
  backgroundOpen: boolean;
  terminalOpen?: boolean;
  backgroundRunningCount: number;
  taskOpen: boolean;
  taskCompleted: number;
  taskTotal: number;
  taskRunning: boolean;
  /** The other conversations: how many run (or wait for approval) and how many finished out of sight. */
  runsOpen: boolean;
  runsLive: number;
  runsFinished: number;
  agentsOpen: boolean;
  agentCount: number;
  reviewOpen: boolean;
  reviewCount: number;
  artifactsOpen: boolean;
  artifactCount: number;
  inspectorOpen: boolean;
  accountAuthenticated: boolean;
  /** The signed-in user's picture, when there is one; the account tool wears it instead of the glyph. */
  accountAvatar?: string;
  /** Keys shown beside a tool's name (the shortcut the user has set for it). */
  shortcuts?: Partial<Record<DockId, string>>;
  onOpenFiles(): void;
  onToggleBrowser(): void;
  onToggleBackground(): void;
  onToggleTerminal?(): void;
  onToggleTasks(): void;
  onToggleRuns(): void;
  onToggleAgents(): void;
  onToggleReview(): void;
  onToggleArtifacts(): void;
  onToggleInspector(): void;
  onOpenProfile(): void;
  onOpenAccount(): void;
  onOpenSettings(): void;
}

type Item = {
  label: string; icon: ReactNode; active?: boolean; disabled?: boolean; reason?: string;
  /** A tool that can carry a count says so (0 is "none yet"); the dot only says there is one. */
  count?: number;
  /** A tool that reports a standing state (signed in). */
  status?: boolean;
  /** Work is running behind the tool. */
  live?: boolean;
  /** A plan's completed/total steps; the tile carries a slim meter, not another number. */
  progress?: { completed: number; total: number };
  onClick(): void;
};

/** A press that may become a drag: everything measured when it starts, so a frame of movement only writes styles. */
type DragSession = {
  id: DockId; pointerId: number; startY: number; from: number; to: number; started: boolean;
  nodes: HTMLElement[]; tops: number[]; height: number; scale: number; low: number; high: number; wanted: number; frame: number;
};

export function RightToolDock(props: RightToolDockProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const reduce = useReducedMotion();
  const presence = useMotionPresence(!props.hidden, RETAIN.fast);
  const [order, setOrder] = useState(readOrder);
  const [hoverId, setHoverId] = useState<DockId | null>(null);
  const [focusId, setFocusId] = useState<DockId | null>(null);
  const [stopId, setStopId] = useState<DockId | null>(null);
  const [pressed, setPressed] = useState(false);
  const [dragId, setDragId] = useState<DockId | null>(null);
  const [hushed, setHushed] = useState<DockId | null>(null);
  const [tip, setTip] = useState<{ id: DockId | null; on: boolean }>({ id: null, on: false });
  const [announcement, setAnnouncement] = useState("");
  const [brokenAvatar, setBrokenAvatar] = useState("");
  const dockRef = useRef<HTMLDivElement>(null);
  const gliderRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const drag = useRef<DragSession | null>(null);
  const suppressClick = useRef(false);
  const settling = useRef(false);
  const latestOrder = useRef(order);
  const flipFrom = useRef<Map<DockId, number> | null>(null);
  const refocus = useRef<DockId | null>(null);
  const tipTimer = useRef(0);
  // The last count each tool showed, so a count that drops to none fades out with its number still on it.
  const shownCounts = useRef(new Map<DockId, number>());
  const tipOn = useRef(false);
  const tipClosedAt = useRef(-1e9);
  const tipWasOn = useRef(false);
  latestOrder.current = order;

  const items: Record<DockId, Item> = {
    files: {
      label: zh ? "项目文件" : "Project files", icon: <FolderOpen size={16} strokeWidth={1.75} />,
      disabled: !props.filesAvailable,
      reason: zh ? "先选择一个项目" : "Select a project first",
      onClick: props.onOpenFiles,
    },
    browser: {
      label: zh ? "内置浏览器" : "Browser", icon: <Globe2 size={16} strokeWidth={1.75} />,
      active: props.browserOpen,
      disabled: !props.browserAvailable,
      reason: zh ? "仅桌面端支持内置浏览器" : "Available in Loom Desktop",
      onClick: props.onToggleBrowser,
    },
    background: {
      label: zh ? "后台任务" : "Background tasks", icon: <Clock3 size={16} strokeWidth={1.75} />,
      active: props.backgroundOpen, disabled: props.loading || !props.hasWorkspace,
      reason: zh ? "请等待对话载入" : "Wait for the conversation",
      count: props.backgroundRunningCount, live: props.backgroundRunningCount > 0,
      onClick: props.onToggleBackground,
    },
    terminal: {
      label: zh ? "终端" : "Terminal", icon: <Terminal size={16} strokeWidth={1.75} />,
      active: Boolean(props.terminalOpen), disabled: props.loading || !props.hasWorkspace,
      reason: zh ? "先选择项目或等待对话载入" : "Select a project or wait for the conversation",
      onClick: () => props.onToggleTerminal?.(),
    },
    tasks: {
      label: props.taskTotal
        ? (zh ? `任务进度 · ${props.taskCompleted}/${props.taskTotal} 已完成` : `Task progress · ${props.taskCompleted}/${props.taskTotal} complete`)
        : (zh ? "任务进度" : "Task progress"),
      icon: <ListChecks size={16} strokeWidth={1.75} />,
      active: props.taskOpen,
      disabled: props.loading || props.taskTotal === 0,
      reason: zh ? "当前对话还没有任务计划" : "No task plan in this conversation",
      live: props.taskRunning,
      progress: props.taskTotal ? { completed: props.taskCompleted, total: props.taskTotal } : undefined,
      onClick: props.onToggleTasks,
    },
    runs: {
      label: zh ? "对话动态" : "Conversations", icon: <MessagesSquare size={16} strokeWidth={1.75} />,
      active: props.runsOpen,
      count: props.runsLive + props.runsFinished, live: props.runsLive > 0,
      onClick: props.onToggleRuns,
    },
    agents: {
      label: zh ? "子代理" : "Sub-agents", icon: <GitFork size={16} strokeWidth={1.75} />,
      active: props.agentsOpen, disabled: props.loading, count: props.agentCount, onClick: props.onToggleAgents,
    },
    review: {
      label: zh ? "代码审查" : "Review", icon: <FileDiff size={16} strokeWidth={1.75} />,
      active: props.reviewOpen, disabled: props.loading, count: props.reviewCount, onClick: props.onToggleReview,
    },
    artifacts: {
      label: zh ? "产物与预览" : "Artifacts & preview", icon: <PanelsTopLeft size={16} strokeWidth={1.75} />,
      active: props.artifactsOpen, disabled: props.loading, count: props.artifactCount, onClick: props.onToggleArtifacts,
    },
    inspector: {
      label: zh ? "运行详情" : "Runtime inspector",
      icon: props.inspectorOpen ? <PanelRightClose size={16} strokeWidth={1.75} />
        : <PanelRightOpen size={16} strokeWidth={1.75} />,
      active: props.inspectorOpen, disabled: props.loading, onClick: props.onToggleInspector,
    },
    profile: {
      label: zh ? "个人主页与使用洞察" : "Profile & insights",
      icon: <Pulse size={16} strokeWidth={1.75} />, onClick: props.onOpenProfile,
    },
    account: {
      label: zh ? "Loom 账号" : "Account",
      // A picture that cannot be drawn (a stale or damaged value from the account service) leaves the tool blank:
      // the glyph takes its place until a different picture arrives.
      icon: props.accountAvatar && props.accountAvatar !== brokenAvatar
        ? <img className="loom-tool-avatar" src={props.accountAvatar} alt="" draggable={false}
            onError={() => setBrokenAvatar(props.accountAvatar ?? "")} />
        : <UserRound size={16} strokeWidth={1.75} />,
      status: props.accountAuthenticated, onClick: props.onOpenAccount,
    },
    settings: {
      label: zh ? "设置" : "Settings", icon: <Settings size={16} strokeWidth={1.75} />,
      onClick: props.onOpenSettings,
    },
  };
  const nameOf = (id: DockId) => {
    const item = items[id];
    // The count lives in the words (the dot only says there is one), where it is read in full.
    return item.count ? `${item.label} · ${item.count > 99 ? "99+" : item.count}` : item.label;
  };

  const enabled = order.filter(id => !items[id].disabled);
  // One tab stop for the whole dock (arrow keys do the rest): the tool last used, or the first one that can be.
  const tabStop = stopId && enabled.includes(stopId) ? stopId : enabled[0] ?? null;
  // The pointer wins over the keyboard; nothing is lit while a tool is being carried.
  const hot = dragId ? null : hoverId ?? focusId;
  const glideId = hot;

  /** Where every slot's top sits (px from the dock's top), as it is painted now, transforms included. */
  const captureTops = (): Map<DockId, number> => {
    const tops = new Map<DockId, number>();
    const dock = dockRef.current;
    if (!dock) return tops;
    const frame = dock.getBoundingClientRect();
    // Painted px back to the layout px a transform is written in (they differ only under the web build's CSS zoom).
    const scale = frame.height / (dock.offsetHeight || 1) || 1;
    dock.querySelectorAll<HTMLElement>(".loom-tool-position").forEach(node => {
      tops.set(node.dataset.slotId as DockId, (node.getBoundingClientRect().top - frame.top) / scale);
    });
    return tops;
  };

  const commitOrder = (next: DockId[], animate: boolean) => {
    if (animate) flipFrom.current = captureTops();
    setOrder(next);
    try { localStorage.setItem(ORDER_KEY, JSON.stringify(next)); }
    catch { /* Order still works until the next reload. */ }
  };

  const announce = (id: DockId, index: number) => {
    const total = latestOrder.current.length;
    setAnnouncement(zh ? `${items[id].label}，第 ${index + 1} 位，共 ${total} 位` : `${items[id].label}, position ${index + 1} of ${total}`);
  };

  // --- Carrying a tool -------------------------------------------------------------------------------------------

  const beginDrag = (session: DragSession): boolean => {
    const dock = dockRef.current;
    if (!dock) return false;
    const nodes = [...dock.querySelectorAll<HTMLElement>(".loom-tool-position")];
    if (nodes.length !== latestOrder.current.length) return false;
    session.nodes = nodes;
    // Layout positions: they ignore what is in flight (a glide from the keyboard) and are in the units transforms use.
    session.tops = nodes.map(node => node.offsetTop);
    session.height = nodes[session.from].offsetHeight;
    session.scale = dock.getBoundingClientRect().height / (dock.offsetHeight || 1) || 1;
    const siblings = latestOrder.current.map((id, at) => toolGroup(id) === toolGroup(session.id) ? at : -1).filter(at => at >= 0);
    session.low = session.tops[siblings[0]] - session.tops[session.from];
    session.high = session.tops[siblings[siblings.length - 1]] - session.tops[session.from];
    nodes[session.from].classList.add("is-lifted");
    dock.dataset.dragging = "true";
    session.started = true;
    setDragId(session.id);
    return true;
  };

  const stepDrag = (session: DragSession) => {
    session.frame = 0;
    if (!session.started || drag.current !== session) return;
    const dy = clamp(session.wanted, session.low - 4, session.high + 4);
    session.nodes[session.from].style.setProperty("--drag-y", `${dy}px`);
    // The slot whose middle is nearest the carried tool's middle is where it will land.
    const middle = session.tops[session.from] + dy + session.height / 2;
    let to = session.from;
    let nearest = Infinity;
    session.tops.forEach((top, index) => {
      if (toolGroup(latestOrder.current[index]) !== toolGroup(session.id)) return;
      const gap = Math.abs(top + session.height / 2 - middle);
      if (gap < nearest) { nearest = gap; to = index; }
    });
    if (to === session.to) return;
    session.to = to;
    // The others make room: whoever sat between the two slots moves one place, by the real distance (the rule counts).
    session.nodes.forEach((node, index) => {
      if (index === session.from) return;
      let slot = index;
      if (session.from < to && index > session.from && index <= to) slot = index - 1;
      else if (session.from > to && index >= to && index < session.from) slot = index + 1;
      node.style.setProperty("--shift", `${session.tops[slot] - session.tops[index]}px`);
    });
  };

  const endDrag = (commit: boolean) => {
    const session = drag.current;
    drag.current = null;
    if (!session) return;
    window.cancelAnimationFrame(session.frame);
    setPressed(false);
    if (!session.started) return;
    // The click that follows the release belongs to the carry, not to the tool (also when Escape put it back and the
    // button is let go later): it is swallowed when it comes, or forgotten at the next press or key.
    suppressClick.current = true;
    const dock = dockRef.current;
    const target = commit ? session.to : session.from;
    settling.current = true;
    if (dock) dock.dataset.dragging = "settling";
    session.nodes[session.from].style.setProperty("--drag-y", `${session.tops[target] - session.tops[session.from]}px`);
    if (!commit) session.nodes.forEach(node => node.style.setProperty("--shift", "0px"));
    const land = () => {
      const next = commit && target !== session.from ? moveItem(latestOrder.current, session.from, target) : null;
      // Everything already stands where the new order puts it, so the styles that carried it go in the same
      // frame as the DOM order changes, with transitions off, and nothing is seen to move.
      if (dock) dock.dataset.dragging = "commit";
      session.nodes.forEach(node => {
        node.style.removeProperty("--shift");
        node.style.removeProperty("--drag-y");
        node.classList.remove("is-lifted");
      });
      flushSync(() => {
        if (next) commitOrder(next, false);
        setDragId(null);
      });
      if (dock) { void dock.offsetWidth; delete dock.dataset.dragging; }
      settling.current = false;
      if (next) announce(session.id, target);
    };
    if (reduce) land();
    else window.setTimeout(land, SETTLE_MS);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>, id: DockId) => {
    suppressClick.current = false;
    setHushed(id);
    if (event.button !== 0 || items[id].disabled || settling.current) return;
    setPressed(true);
    drag.current = {
      id, pointerId: event.pointerId, startY: event.clientY, from: order.indexOf(id), to: order.indexOf(id),
      started: false, nodes: [], tops: [], height: 0, scale: 1, low: 0, high: 0, wanted: 0, frame: 0,
    };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* The press still works as a click. */ }
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const session = drag.current;
    if (!session || session.pointerId !== event.pointerId) return;
    const dy = event.clientY - session.startY;
    if (!session.started && (Math.abs(dy) < DRAG_THRESHOLD || !beginDrag(session))) return;
    session.wanted = dy / session.scale;
    if (!session.frame) session.frame = window.requestAnimationFrame(() => stepDrag(session));
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (drag.current && drag.current.pointerId !== event.pointerId) return;
    if (drag.current?.started) endDrag(true);
    else { drag.current = null; setPressed(false); }
  };

  const handleKey = (event: KeyboardEvent<HTMLButtonElement>, id: DockId) => {
    if (!drag.current?.started) suppressClick.current = false;
    if (event.key === "Escape" && drag.current?.started) {
      event.preventDefault();
      endDrag(false);
      return;
    }
    if (drag.current?.started || settling.current) return;
    // A name that appeared on its own can be sent away without moving the pointer or the focus.
    if (event.key === "Escape") { setHushed(id); return; }
    const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
    if (!vertical && event.key !== "Home" && event.key !== "End") return;
    const index = order.indexOf(id);
    if (event.altKey && vertical) {
      const next = index + (event.key === "ArrowUp" ? -1 : 1);
      if (next < 0 || next >= order.length || toolGroup(order[next]) !== toolGroup(id)) return;
      event.preventDefault();
      refocus.current = id;
      commitOrder(moveItem(order, index, next), true);
      announce(id, next);
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    let target: DockId | undefined;
    if (event.key === "Home") target = enabled[0];
    else if (event.key === "End") target = enabled[enabled.length - 1];
    else {
      const step = event.key === "ArrowUp" ? -1 : 1;
      for (let at = index + step; at >= 0 && at < order.length; at += step) {
        if (!items[order[at]].disabled) { target = order[at]; break; }
      }
    }
    if (target) dockRef.current?.querySelector<HTMLButtonElement>(`[data-tool-id="${target}"]`)?.focus();
  };

  // --- Light, name and order that follow the pointer ---------------------------------------------------------------

  // The glider: one wash that slides to whichever tool is lit. It comes in without sliding from wherever it last was.
  useLayoutEffect(() => {
    const glider = gliderRef.current;
    const dock = dockRef.current;
    if (!glider || !dock) return;
    const button = glideId ? dock.querySelector<HTMLElement>(`[data-tool-id="${glideId}"]`) : null;
    if (!button) { glider.dataset.on = "false"; return; }
    const slot = button.offsetParent as HTMLElement | null;
    const y = (slot?.offsetTop ?? 0) + button.offsetTop;
    const wasOn = glider.dataset.on === "true";
    if (!wasOn) glider.dataset.park = "true";
    glider.style.setProperty("--glide-y", `${y}px`);
    if (!wasOn) { void glider.offsetWidth; delete glider.dataset.park; }
    glider.dataset.on = "true";
  }, [glideId, order, presence.mounted]);

  // The name: it waits for a pointer that rests, and once one is open the next follows at once. Keyboard focus shows it
  // immediately.
  useEffect(() => {
    window.clearTimeout(tipTimer.current);
    const show = (id: DockId) => { tipOn.current = true; setTip({ id, on: true }); };
    const target = hot && hot !== hushed ? hot : null;
    if (!target) {
      if (tipOn.current) {
        tipOn.current = false;
        tipClosedAt.current = performance.now();
        setTip(previous => ({ ...previous, on: false }));
      }
      return;
    }
    if (tipOn.current || hoverId === null || performance.now() - tipClosedAt.current < TIP_WARM) show(target);
    else tipTimer.current = window.setTimeout(() => show(target), TIP_DELAY);
    return () => window.clearTimeout(tipTimer.current);
  }, [hot, hushed, hoverId]);

  useLayoutEffect(() => {
    const note = tipRef.current;
    const dock = dockRef.current;
    const first = !tipWasOn.current;
    tipWasOn.current = tip.on;
    if (!note || !dock || !tip.id) return;
    const button = dock.querySelector<HTMLElement>(`[data-tool-id="${tip.id}"]`);
    if (!button) return;
    // Layout px from the stage's top: the dock sits there (a window too short for it scrolls the dock).
    const slot = button.offsetParent as HTMLElement | null;
    const y = dock.offsetTop - dock.scrollTop + (slot?.offsetTop ?? 0) + button.offsetTop + button.offsetHeight / 2;
    if (first) note.dataset.park = "true";
    note.style.setProperty("--tip-y", `${y}px`);
    if (first) { void note.offsetWidth; delete note.dataset.park; }
  }, [tip.id, tip.on, order, presence.mounted]);

  // Tools that changed places by keyboard glide there (a carry already stands in its place).
  useLayoutEffect(() => {
    const before = flipFrom.current;
    flipFrom.current = null;
    const dock = dockRef.current;
    if (!before || !dock || reduce || typeof Element.prototype.animate !== "function") return;
    const after = captureTops();
    dock.querySelectorAll<HTMLElement>(".loom-tool-position").forEach(node => {
      const id = node.dataset.slotId as DockId;
      const delta = (before.get(id) ?? NaN) - (after.get(id) ?? NaN);
      if (!Number.isFinite(delta) || Math.abs(delta) < 0.5) return;
      node.animate([{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }], { duration: DUR.flow, easing: SPRING.settle });
    });
  }, [order]);

  // React moves a node to reorder it, and a node that moves loses focus: give it back.
  useLayoutEffect(() => {
    const id = refocus.current;
    refocus.current = null;
    if (id) dockRef.current?.querySelector<HTMLButtonElement>(`[data-tool-id="${id}"]`)?.focus({ preventScroll: true });
  }, [order]);

  useEffect(() => () => {
    window.clearTimeout(tipTimer.current);
    if (drag.current) window.cancelAnimationFrame(drag.current.frame);
  }, []);

  if (!presence.mounted) return null;

  const countLabel = (id: DockId, count: number) => {
    if (count > 0) shownCounts.current.set(id, count);
    const shown = shownCounts.current.get(id);
    return shown === undefined ? "" : shown > 9 ? "9+" : shown;
  };

  const tipItem = tip.id ? items[tip.id] : null;
  const tipKeys = tip.id && !items[tip.id].disabled ? shortcutKeys(props.shortcuts?.[tip.id]) : [];
  const rows = order.map((id, index) => {
    const item = items[id];
    const ratio = item.progress && item.progress.total
      ? Math.max(0, Math.min(1, item.progress.completed / item.progress.total)) : 0;
    return (
      <div
        className="loom-tool-position"
        key={id}
        data-slot-id={id}
        data-disabled={item.disabled ? "true" : undefined}
        style={{ "--i": index } as CSSProperties}
        onPointerEnter={() => { setHoverId(id); setHushed(previous => (previous === id ? previous : null)); }}
      >
        <button
          type="button"
          className={"loom-tool-button" + (item.active ? " is-active" : "")}
          data-tool-id={id}
          data-ic-live={item.live ? "true" : undefined}
          aria-label={nameOf(id)}
          aria-description={item.disabled ? item.reason : undefined}
          aria-pressed={typeof item.active === "boolean" ? item.active : undefined}
          tabIndex={id === tabStop ? 0 : -1}
          disabled={item.disabled}
          onClick={event => {
            if (suppressClick.current) { suppressClick.current = false; event.preventDefault(); return; }
            item.onClick();
          }}
          onKeyDown={event => handleKey(event, id)}
          onFocus={event => {
            setStopId(id);
            setFocusId(event.currentTarget.matches(":focus-visible") ? id : null);
          }}
          onBlur={() => setFocusId(previous => (previous === id ? null : previous))}
          onPointerDown={event => handlePointerDown(event, id)}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={() => endDrag(false)}
          onDragStart={event => event.preventDefault()}
        >
          {item.icon}
          {item.progress ? (
            <span className="loom-tool-progress" data-complete={ratio >= 1 ? "true" : "false"} aria-hidden="true">
              <span style={{ transform: `scaleX(${ratio})` }} />
            </span>
          ) : null}
          {/* The dot stays in the button and says on or off (right-tool-dock.css fades and grows it either way): drawn only
              while there was a count, it popped in and out. */}
          {item.count !== undefined
            ? <span className="loom-tool-count dock-numeric" data-on={item.count > 0 ? "true" : "false"} data-live={item.live && item.count > 0 ? "true" : undefined} aria-hidden="true">{countLabel(id, item.count)}</span>
            : null}
          {item.status !== undefined
            ? <span className="loom-tool-status" data-on={item.status ? "true" : "false"} aria-hidden="true" />
            : null}
        </button>
      </div>
    );
  });
  // The rules belong to the slots where a group starts, so a tool dragged across one joins the other group.
  const utilityAt = order.findIndex(id => toolGroup(id) === 2);
  const activityAt = order.findIndex(id => toolGroup(id) === 1);
  if (utilityAt > 0) rows.splice(utilityAt, 0, <span className="loom-tool-divider" key="divider" aria-hidden="true"></span>);
  if (activityAt > 0) rows.splice(activityAt, 0, <span className="loom-tool-divider" key="activity-divider" aria-hidden="true"></span>);

  return (
    <aside className="loom-tool-rail" aria-label={zh ? "快捷工具" : "Quick tools"} data-motion-phase={presence.phase}>
      <div className="loom-tool-stage">
        <div
          className="loom-tool-dock"
          ref={dockRef}
          role="toolbar"
          aria-orientation="vertical"
          aria-label={zh ? "工作工具（拖动或 Alt+↑/↓ 在组内排序）" : "Workspace tools (drag or Alt+Up/Down to reorder within a group)"}
          data-press={pressed ? "true" : undefined}
          onPointerLeave={() => { setHoverId(null); setHushed(null); if (!drag.current) setPressed(false); }}
          onScroll={() => setHushed(tip.id)}
        >
          <span className="loom-tool-glider" ref={gliderRef} data-on="false" aria-hidden="true" />
          {rows}
        </div>
        <div className="loom-tool-tip" ref={tipRef} data-on={tip.on ? "true" : "false"} aria-hidden="true">
          {tipItem ? (
            <span className="loom-tool-tip-body" key={tip.id}>
              <span className="loom-tool-tip-text">
                <span className="loom-tool-tip-label">{tipItem.label}</span>
                {tipItem.disabled && tipItem.reason ? <span className="loom-tool-tip-note">{tipItem.reason}</span> : null}
              </span>
              {!tipItem.disabled && tipItem.count
                ? <span className="loom-tool-tip-count">{tipItem.count > 99 ? "99+" : tipItem.count}</span> : null}
              {tipKeys.length
                ? <span className="loom-tool-keys">{tipKeys.map((key, at) => <kbd key={at}>{key}</kbd>)}</span> : null}
            </span>
          ) : null}
        </div>
      </div>
      <span className="loom-tool-sr" role="status" aria-live="polite">{announcement}</span>
    </aside>
  );
}

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { useMotionPresence } from "../motion/useMotionPresence";
import type { TranscriptItem } from "../types/loom";
import { isSubAgentToolItem, SubAgentWorkspace } from "./SubAgentWorkspace";
import "./sub-agent-dock.css";

interface SubAgentDockProps {
  items: TranscriptItem[];
  open: boolean;
  active: boolean;
  onClose(): void;
}

const AGENT_DOCK_WIDTH_KEY = "loom.layout.agentDockWidth";
const AGENT_DOCK_DEFAULT = 410;

function bounds(): { min: number; max: number } {
  const viewport = Math.max(360, window.innerWidth || 0);
  if (viewport <= 720) {
    return {
      min: Math.min(300, viewport * 0.78),
      max: Math.max(300, viewport * 0.92),
    };
  }
  return {
    min: 340,
    max: Math.max(340, Math.min(560, viewport - 560)),
  };
}

function clampWidth(value: number): number {
  const { min, max } = bounds();
  return Math.round(Math.min(max, Math.max(min, value)));
}

function readDockWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(AGENT_DOCK_WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampWidth(stored);
  } catch {
    // Keep the default when persistence is unavailable.
  }
  return clampWidth(AGENT_DOCK_DEFAULT);
}

function persistDockWidth(value: number): void {
  try {
    window.localStorage.setItem(AGENT_DOCK_WIDTH_KEY, String(Math.round(value)));
  } catch {
    // Resizing still works for the current session.
  }
}

export function SubAgentDock({ items, open, active, onClose }: SubAgentDockProps) {
  const presence = useMotionPresence(open, 420);
  const agentItems = useMemo(() => items.filter(isSubAgentToolItem), [items]);
  const [width, setWidth] = useState(readDockWidth);
  const resizeRef = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
    currentWidth: number;
  } | null>(null);

  useEffect(() => {
    document.documentElement.style.setProperty("--agent-pane-width", `${width}px`);
    return () => {
      document.documentElement.style.removeProperty("--agent-pane-width");
    };
  }, [width]);

  useEffect(() => {
    const handleResize = () => setWidth((current) => clampWidth(current));
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      document.body.classList.remove("loom-agent-dock-resizing");
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    document.documentElement.dataset.loomAgentsOpen = "true";
    return () => {
      window.removeEventListener("keydown", handleKey);
      delete document.documentElement.dataset.loomAgentsOpen;
    };
  }, [onClose, open]);

  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    resizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: width,
      currentWidth: width,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add("loom-agent-dock-resizing");
    event.preventDefault();
  };

  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    const next = clampWidth(session.startWidth + (session.startX - event.clientX));
    session.currentWidth = next;
    const translated = session.startWidth - next;
    event.currentTarget.style.transform = `translateX(${translated}px)`;
    event.currentTarget.setAttribute("aria-valuenow", String(next));
    event.preventDefault();
  };

  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    resizeRef.current = null;
    event.currentTarget.style.transform = "";
    event.currentTarget.setAttribute("aria-valuenow", String(session.currentWidth));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setWidth(session.currentWidth);
    document.body.classList.remove("loom-agent-dock-resizing");
    persistDockWidth(session.currentWidth);
  };

  const resizeWithKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const amount = event.shiftKey ? 32 : 12;
    const direction = event.key === "ArrowLeft" ? 1 : -1;
    const next = clampWidth(width + direction * amount);
    setWidth(next);
    persistDockWidth(next);
  };

  const resetWidth = () => {
    const next = clampWidth(AGENT_DOCK_DEFAULT);
    setWidth(next);
    try {
      window.localStorage.removeItem(AGENT_DOCK_WIDTH_KEY);
    } catch {
      // Ignore storage failures.
    }
  };

  if (!presence.mounted) return null;

  return createPortal(
    <aside className="sub-agent-dock" data-motion-phase={presence.phase} data-open={open ? "true" : "false"} aria-label="子代理工作区">
      <div
        className="sub-agent-dock-resizer"
        role="separator"
        aria-label="调整子代理工作区宽度"
        aria-orientation="vertical"
        aria-valuemin={Math.round(bounds().min)}
        aria-valuemax={Math.round(bounds().max)}
        aria-valuenow={width}
        tabIndex={0}
        title="拖动调整宽度 · 双击恢复默认"
        onPointerDown={beginResize}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={resizeWithKeyboard}
        onDoubleClick={resetWidth}
      />
      <SubAgentWorkspace
        items={agentItems}
        active={active || open}
        docked
        onClose={onClose}
      />
    </aside>,
    document.body,
  );
}

import { useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import type { TranscriptItem } from "../types/loom";
import { isSubAgentToolItem, SubAgentWorkspace } from "./SubAgentWorkspace";
import "./sub-agent-dock.css";

interface SubAgentDockProps {
  items: TranscriptItem[];
  open: boolean;
  active: boolean;
  onClose(): void;
}

export function SubAgentDock({ items, open, active, onClose }: SubAgentDockProps) {
  const agentItems = useMemo(() => items.filter(isSubAgentToolItem), [items]);

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

  if (!open) return null;

  return createPortal(
    <aside className="sub-agent-dock" aria-label="子代理工作区">
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

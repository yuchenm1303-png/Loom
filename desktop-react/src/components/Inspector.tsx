import { Activity, Files, PanelRightClose, Terminal } from "lucide-react";
import { useMemo, useState } from "react";
import type { TranscriptItem } from "../types/loom";

interface InspectorProps {
  items: TranscriptItem[];
  onClose(): void;
}

type Tab = "activity" | "changes" | "terminal";

export function Inspector({ items, onClose }: InspectorProps) {
  const [tab, setTab] = useState<Tab>("activity");
  const toolItems = useMemo(() => items.filter((item) => ["tool_call", "process", "approval", "error"].includes(item.type)), [items]);
  const changes = useMemo(() => items.filter((item) => item.type === "file_edit"), [items]);
  const processes = useMemo(() => items.filter((item) => item.type === "process"), [items]);
  const visible = tab === "changes" ? changes : tab === "terminal" ? processes : toolItems;

  return (
    <aside className="inspector">
      <div className="inspector-header">
        <strong>Runtime</strong>
        <button className="icon-button" onClick={onClose} title="Close inspector"><PanelRightClose size={16} /></button>
      </div>
      <div className="inspector-tabs">
        <button className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}><Activity size={14} />Activity</button>
        <button className={tab === "changes" ? "active" : ""} onClick={() => setTab("changes")}><Files size={14} />Changes</button>
        <button className={tab === "terminal" ? "active" : ""} onClick={() => setTab("terminal")}><Terminal size={14} />Shell</button>
      </div>
      <div className="inspector-body">
        {!visible.length ? <div className="inspector-empty">Nothing here yet.</div> : visible.map((item) => (
          <div className="inspector-row" key={item.id}>
            <div className="inspector-row-title">{item.toolName || (item.type === "file_edit" ? "Workspace change" : item.type.replaceAll("_", " "))}</div>
            <div className="inspector-row-meta">{item.status || "completed"}</div>
          </div>
        ))}
      </div>
    </aside>
  );
}

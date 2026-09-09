import { Check, ChevronRight, CircleAlert, FileDiff, Terminal, Wrench } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { TranscriptItem } from "../types/loom";

interface TranscriptProps {
  items: TranscriptItem[];
  onApproval(item: TranscriptItem, approved: boolean): void;
}

function splitReasoning(text: string): { reasoning: string; answer: string } {
  const match = text.match(/<think>([\s\S]*?)<\/think>([\s\S]*)/i);
  if (!match) return { reasoning: "", answer: text };
  return { reasoning: match[1].trim(), answer: match[2].trimStart() };
}

function Disclosure({ label, children, openByDefault = false }: { label: string; children: ReactNode; openByDefault?: boolean }) {
  const [open, setOpen] = useState(openByDefault);
  return (
    <div className={`disclosure ${open ? "open" : ""}`}>
      <button className="disclosure-trigger" onClick={() => setOpen((value) => !value)}>
        <ChevronRight size={14} className="disclosure-chevron" />
        <span>{label}</span>
      </button>
      <div className="disclosure-grid">
        <div className="disclosure-inner">{children}</div>
      </div>
    </div>
  );
}

function ToolItem({ item }: { item: TranscriptItem }) {
  const detail = item.content || item.stdout || item.stderr || (item.arguments ? JSON.stringify(item.arguments, null, 2) : "");
  return (
    <div className="activity-row">
      <div className="activity-line">
        <Wrench size={15} />
        <span className="activity-title">{item.toolName || "Tool"}</span>
        <span className={`activity-status ${item.status ?? ""}`}>{item.status || ""}</span>
      </div>
      {detail ? <Disclosure label="Details"><pre className="activity-output">{detail}</pre></Disclosure> : null}
    </div>
  );
}

function ProcessItem({ item }: { item: TranscriptItem }) {
  const command = Array.isArray(item.argv) ? item.argv.join(" ") : String(item.command ?? "Process");
  const output = `${item.stdout ?? ""}${item.stderr ? `\n${item.stderr}` : ""}`;
  return (
    <div className="activity-row">
      <div className="activity-line">
        <Terminal size={15} />
        <span className="activity-title">{command}</span>
        <span className={`activity-status ${item.status ?? ""}`}>{item.status || ""}</span>
      </div>
      {output ? <Disclosure label="Shell output" openByDefault={item.status === "running"}><pre className="activity-output">{output}</pre></Disclosure> : null}
    </div>
  );
}

function ItemView({ item, onApproval }: { item: TranscriptItem; onApproval(item: TranscriptItem, approved: boolean): void }) {
  if (item.type === "user_message") return <div className="user-message">{item.text}</div>;
  if (item.type === "assistant_message") {
    const { reasoning, answer } = splitReasoning(item.text ?? "");
    return (
      <div className="assistant-message">
        {reasoning ? <Disclosure label="Thought process"><div className="reasoning-copy">{reasoning}</div></Disclosure> : null}
        {answer ? <div className="assistant-copy">{answer}</div> : <div className="streaming-placeholder">Thinking…</div>}
      </div>
    );
  }
  if (item.type === "tool_call") return <ToolItem item={item} />;
  if (item.type === "process") return <ProcessItem item={item} />;
  if (item.type === "file_edit") return (
    <div className="activity-row">
      <div className="activity-line"><FileDiff size={15} /><span className="activity-title">Edited {(item.paths ?? []).join(", ") || "workspace"}</span></div>
      {item.diff ? <Disclosure label="View diff"><pre className="diff-output">{item.diff}</pre></Disclosure> : null}
    </div>
  );
  if (item.type === "approval") return (
    <div className="approval-card">
      <div className="approval-title"><CircleAlert size={16} /> Permission required</div>
      <div className="approval-copy">{item.toolName || "Tool"}{item.reason ? ` · ${item.reason}` : ""}</div>
      <div className="approval-actions">
        <button className="button secondary" onClick={() => onApproval(item, false)}>Deny</button>
        <button className="button primary" onClick={() => onApproval(item, true)}><Check size={14} /> Allow</button>
      </div>
    </div>
  );
  if (item.type === "error") return <div className="error-row"><CircleAlert size={15} />{item.error || "Turn failed"}</div>;
  return null;
}

export function Transcript({ items, onApproval }: TranscriptProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousCount = useRef(0);

  useEffect(() => {
    if (items.length > previousCount.current) {
      const scroller = scrollRef.current;
      if (scroller) {
        requestAnimationFrame(() => {
          scroller.scrollTop = scroller.scrollHeight;
        });
      }
    }
    previousCount.current = items.length;
  }, [items.length]);

  return (
    <div className="transcript-scroll" ref={scrollRef}>
      <main className="transcript" aria-live="polite">
        {!items.length ? (
          <div className="empty-state">
            <div className="empty-mark">L</div>
            <h1>What are we working on?</h1>
            <p>Ask Loom to inspect a project, change code, use tools, or work through a task.</p>
          </div>
        ) : items.map((item) => <ItemView key={item.id} item={item} onApproval={onApproval} />)}
      </main>
    </div>
  );
}

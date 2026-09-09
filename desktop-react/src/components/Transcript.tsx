import {
  ArrowUpRight,
  Bug,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  FileDiff,
  Search,
  Sparkles,
  Terminal,
  Wrench,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { TranscriptItem } from "../types/loom";

interface TranscriptProps {
  items: TranscriptItem[];
  promptDisabled?: boolean;
  onPrompt?(prompt: string): void;
  onApproval(item: TranscriptItem, approved: boolean): void;
}

const starterPrompts = [
  {
    icon: Search,
    eyebrow: "Understand",
    title: "Inspect this project",
    copy: "Map the architecture and tell me what matters first.",
    prompt: "Inspect this project, map the architecture, and tell me what I should understand first.",
  },
  {
    icon: Code2,
    eyebrow: "Build",
    title: "Implement a feature",
    copy: "Turn a product idea into a focused code change.",
    prompt: "Help me implement a feature in this project. Start by identifying the smallest clean approach.",
  },
  {
    icon: Bug,
    eyebrow: "Debug",
    title: "Trace a problem",
    copy: "Follow the failure to its root cause before changing code.",
    prompt: "Investigate the current project for the problem I am seeing and trace it to the root cause before making changes.",
  },
  {
    icon: Zap,
    eyebrow: "Automate",
    title: "Run a workflow",
    copy: "Use tools and the workspace to complete a multi-step task.",
    prompt: "Use the available tools and workspace to complete a useful multi-step task for this project.",
  },
] as const;

function splitReasoning(text: string): { reasoning: string; answer: string } {
  const match = text.match(/<think>([\s\S]*?)<\/think>([\s\S]*)/i);
  if (!match) return { reasoning: "", answer: text };
  return { reasoning: match[1].trim(), answer: match[2].trimStart() };
}

function Disclosure({ label, children, openByDefault = false }: { label: string; children: ReactNode; openByDefault?: boolean }) {
  const [open, setOpen] = useState(openByDefault);
  return (
    <div className={`disclosure ${open ? "open" : ""}`}>
      <button className="disclosure-trigger" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <ChevronRight size={14} className="disclosure-chevron" />
        <span>{label}</span>
      </button>
      <div className="disclosure-grid">
        <div className="disclosure-inner">{children}</div>
      </div>
    </div>
  );
}

function ActivityIcon({ running, children }: { running: boolean; children: ReactNode }) {
  return (
    <span className={`activity-icon-shell ${running ? "is-running" : ""}`}>
      {children}
      {running ? <span className="activity-pulse" /> : null}
    </span>
  );
}

function ToolItem({ item }: { item: TranscriptItem }) {
  const detail = item.content || item.stdout || item.stderr || (item.arguments ? JSON.stringify(item.arguments, null, 2) : "");
  const running = item.status === "running" || item.status === "started";
  return (
    <div className={`activity-row ${running ? "is-running" : ""}`}>
      <div className="activity-line">
        <ActivityIcon running={running}><Wrench size={13} /></ActivityIcon>
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
  const running = item.status === "running" || item.status === "started";
  return (
    <div className={`activity-row ${running ? "is-running" : ""}`}>
      <div className="activity-line">
        <ActivityIcon running={running}><Terminal size={13} /></ActivityIcon>
        <span className="activity-title">{command}</span>
        <span className={`activity-status ${item.status ?? ""}`}>{item.status || ""}</span>
      </div>
      {output ? <Disclosure label="Shell output" openByDefault={item.status === "running"}><pre className="activity-output">{output}</pre></Disclosure> : null}
    </div>
  );
}

function StreamingIndicator() {
  return (
    <div className="streaming-placeholder" role="status">
      <span className="streaming-spark"><Sparkles size={13} /></span>
      <span>Loom is working</span>
      <span className="streaming-dots" aria-hidden="true"><i /><i /><i /></span>
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
        {answer ? <div className="assistant-copy">{answer}</div> : <StreamingIndicator />}
      </div>
    );
  }
  if (item.type === "tool_call") return <ToolItem item={item} />;
  if (item.type === "process") return <ProcessItem item={item} />;
  if (item.type === "file_edit") return (
    <div className="activity-row file-edit-row">
      <div className="activity-line">
        <ActivityIcon running={false}><FileDiff size={13} /></ActivityIcon>
        <span className="activity-title">Edited {(item.paths ?? []).join(", ") || "workspace"}</span>
        <span className="activity-status completed">changed</span>
      </div>
      {item.diff ? <Disclosure label="View diff"><pre className="diff-output">{item.diff}</pre></Disclosure> : null}
    </div>
  );
  if (item.type === "approval") return (
    <div className="approval-card">
      <div className="approval-icon"><CircleAlert size={16} /></div>
      <div className="approval-main">
        <div className="approval-title">Permission required</div>
        <div className="approval-copy">{item.toolName || "Tool"}{item.reason ? ` · ${item.reason}` : ""}</div>
      </div>
      <div className="approval-actions">
        <button className="button secondary" onClick={() => onApproval(item, false)}>Deny</button>
        <button className="button primary" onClick={() => onApproval(item, true)}><Check size={14} /> Allow</button>
      </div>
    </div>
  );
  if (item.type === "error") return <div className="error-row"><span className="error-icon"><CircleAlert size={14} /></span><span>{item.error || "Turn failed"}</span></div>;
  return null;
}

function EmptyState({ disabled, onPrompt }: { disabled?: boolean; onPrompt?(prompt: string): void }) {
  return (
    <section className="empty-state">
      <div className="empty-hero" aria-hidden="true">
        <div className="empty-orbit orbit-one" />
        <div className="empty-orbit orbit-two" />
        <div className="empty-mark"><span>L</span></div>
        <span className="empty-spark spark-one" />
        <span className="empty-spark spark-two" />
      </div>
      <div className="empty-kicker"><Sparkles size={12} /> Loom workspace</div>
      <h1>What are we working on?</h1>
      <p>Inspect a codebase, make a change, debug a failure, or hand Loom a multi-step task.</p>

      <div className="starter-grid">
        {starterPrompts.map(({ icon: Icon, eyebrow, title, copy, prompt }) => (
          <button
            className="starter-card"
            type="button"
            key={title}
            disabled={disabled}
            onClick={() => onPrompt?.(prompt)}
          >
            <span className="starter-icon"><Icon size={15} /></span>
            <span className="starter-content">
              <span className="starter-eyebrow">{eyebrow}</span>
              <strong>{title}</strong>
              <span>{copy}</span>
            </span>
            <ArrowUpRight className="starter-arrow" size={14} />
          </button>
        ))}
      </div>
    </section>
  );
}

export function Transcript({ items, promptDisabled, onPrompt, onApproval }: TranscriptProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousCount = useRef(0);

  useEffect(() => {
    if (items.length > previousCount.current) {
      const scroller = scrollRef.current;
      if (scroller) {
        requestAnimationFrame(() => {
          scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
        });
      }
    }
    previousCount.current = items.length;
  }, [items.length]);

  return (
    <div className="transcript-scroll" ref={scrollRef}>
      <div className="chat-ambient" aria-hidden="true">
        <span className="ambient-glow glow-one" />
        <span className="ambient-glow glow-two" />
        <span className="ambient-grid" />
      </div>
      <main className="transcript" aria-live="polite">
        {!items.length ? (
          <EmptyState disabled={promptDisabled} onPrompt={onPrompt} />
        ) : items.map((item) => (
          <div className={`transcript-entry entry-${item.type}`} key={item.id}>
            <ItemView item={item} onApproval={onApproval} />
          </div>
        ))}
      </main>
    </div>
  );
}

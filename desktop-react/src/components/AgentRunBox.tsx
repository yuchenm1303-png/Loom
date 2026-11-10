/* The small run box of a sub-agent: its latest steps and, once it ended, what it handed back. Not the transcript a
 * main agent shows: a few quiet rows, enough to see what it is doing. Shared by the hub's cards and the phone. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { agentRoleName, durationText, outcomeText, tokensText, type AgentHistoryPage, type AgentHistoryRow, type AgentPeek } from "./agentPeek";
import { historyEntry } from "./agentPeek";
import "./agent-run.css";

const POLL_MS = 1500;
const HISTORY_POLL_MS = 15000;

/** The agent's peek, read now and again every moment while it runs; one last read once it has ended. */
export function useAgentPeek(load: ((agentId: string) => Promise<AgentPeek>) | undefined, agentId: string | null) {
  const [peek, setPeek] = useState<AgentPeek | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setPeek(null);
    setError("");
    if (!load || !agentId) return;
    let cancelled = false;
    let timer = 0;
    const read = async () => {
      try {
        const next = await load(agentId);
        if (cancelled) return;
        setPeek(next);
        setError("");
        if (!next.finished) timer = window.setTimeout(read, POLL_MS);
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        timer = window.setTimeout(read, POLL_MS * 4);
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [load, agentId]);
  return { peek, error };
}

export function AgentRunBox({ peek, error, lang = "zh", now }: { peek: AgentPeek | null; error?: string; lang?: "zh" | "en"; now?: number }) {
  const rows = useRef<HTMLOListElement>(null);
  const following = useRef(true);
  const count = peek?.rows.length ?? 0;
  // New rows follow the bottom unless the person scrolled up to read.
  useLayoutEffect(() => {
    const list = rows.current;
    if (list && following.current) list.scrollTop = list.scrollHeight;
  }, [count, peek?.rows[count - 1]?.text, peek?.rows[count - 1]?.ok]);
  if (!peek) {
    return <div className="ar-box" data-empty="" aria-busy={!error}>
      <p className="ar-note">{error ? (lang === "en" ? `Could not read it: ${error}` : `读取失败：${error}`) : (lang === "en" ? "Reading…" : "读取中…")}</p>
    </div>;
  }
  const clock = now ?? Date.now();
  const facts = [outcomeText(peek.outcome, lang), durationText(peek.startedAt, peek.endedAt, clock, lang), tokensText(peek.tokens)].filter(Boolean).join(" · ");
  return (
    <div className="ar-box" data-state={peek.finished ? peek.outcome || "done" : "running"} role="group"
         aria-label={agentRoleName(peek.role, lang)}>
      {peek.rows.length ? (
        <ol className="ar-rows" ref={rows} onScroll={(event) => {
          const list = event.currentTarget;
          following.current = list.scrollHeight - list.scrollTop - list.clientHeight < 24;
        }}>
          {peek.rows.map((row, index) => (
            <li key={`${index}:${row.at ?? ""}`} className="ar-row" data-kind={row.kind}
                data-running={row.running || undefined} data-ok={row.ok === false ? "no" : undefined}>
              <i aria-hidden="true" />
              <span title={row.text}>{row.text}</span>
            </li>
          ))}
        </ol>
      ) : <p className="ar-note">{peek.finished ? (lang === "en" ? "No steps were recorded." : "没有记录到步骤。") : (lang === "en" ? "Starting…" : "刚开始…")}</p>}
      {peek.result ? <p className="ar-result">{peek.result}</p> : null}
      {peek.error ? <p className="ar-result" data-error="">{peek.error}</p> : null}
      <p className="ar-facts" role="status">{facts}</p>
    </div>
  );
}

/** Every sub-agent this conversation ever had, newest first; each opens into the same run box. */
export function AgentHistory({ loadPage, loadPeek, lang = "zh" }: {
  loadPage: (offset: number) => Promise<AgentHistoryPage>;
  loadPeek?: (agentId: string) => Promise<AgentPeek>;
  lang?: "zh" | "en";
}) {
  const [rows, setRows] = useState<AgentHistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const { peek, error: peekError } = useAgentPeek(loadPeek, openId);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const read = async () => {
      try {
        const page = await loadPage(0);
        if (cancelled) return;
        // The newest page replaces what it covers, so a record that just ended updates; rows the person loaded with
        // "more" stay where they are.
        setRows((current) => {
          const fresh = new Set(page.agents.map((row) => row.id));
          return [...page.agents, ...current.filter((row) => !fresh.has(row.id))];
        });
        setTotal(page.total);
        setError("");
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!cancelled) { setLoaded(true); timer = window.setTimeout(read, HISTORY_POLL_MS); }
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [loadPage]);

  const more = async () => {
    try {
      const page = await loadPage(rows.length);
      setRows((current) => [...current, ...page.agents.filter((row) => !current.some((item) => item.id === row.id))]);
      setTotal(page.total);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  const now = Date.now();
  if (!rows.length) {
    return <p className="ar-note ar-history-empty">{error ? (lang === "en" ? `Could not read the record: ${error}` : `读取记录失败：${error}`)
      : !loaded ? (lang === "en" ? "Reading…" : "读取中…") : (lang === "en" ? "No sub-agent has run in this conversation yet." : "这个对话还没有跑过子代理。")}</p>;
  }
  return (
    <div className="ar-history">
      <ul className="ar-history-list">
        {rows.map((row) => {
          const entry = historyEntry(row, now, lang);
          const open = openId === row.id;
          return (
            <li key={row.id} data-outcome={entry.outcome || "running"} data-open={open || undefined}>
              <button type="button" className="ar-history-row" aria-expanded={open} onClick={() => setOpenId(open ? null : row.id)}>
                <i aria-hidden="true" />
                <span className="ar-history-copy">
                  <strong title={entry.title}>{entry.title}</strong>
                  {entry.headline ? <span className="ar-history-headline" title={entry.headline}>{entry.headline}</span> : null}
                  <small>{entry.meta}</small>
                </span>
              </button>
              {open ? <AgentRunBox peek={peek} error={peekError} lang={lang} now={now} /> : null}
            </li>
          );
        })}
      </ul>
      {rows.length < total ? <button type="button" className="ar-more" onClick={() => void more()}>{lang === "en" ? `Show more (${total - rows.length})` : `再显示 ${total - rows.length} 条`}</button> : null}
    </div>
  );
}

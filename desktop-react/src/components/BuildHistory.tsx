import { useEffect, useRef, useState } from "react";
import { Search, X, Plus } from "lucide-react";
import "./build-history.css";

export interface BuildHistoryEntry {
  id: string; title: string; when: string; current: boolean; live: boolean;
  workspace?: string; projectId?: string; projectName?: string; objective?: string;
  status?: "draft" | "ready" | "running" | "paused" | "needs_you" | "completed" | "cancelled";
  done?: number; total?: number; milestone?: number;
}

const copy = {
  zh: { project: "当前项目", all: "全部项目", search: "搜索标题、目标或项目", empty: "此项目还没有建造对话", noMatch: "没有匹配的对话",
    new: "在当前项目新建对话", more: "查看全部", title: "建造对话", close: "关闭记录", current: "当前对话", unfiled: "未绑定项目",
    draft: "尚未创建任务", ready: "待启动", running: "执行中", paused: "已暂停", needs_you: "待你处理", completed: "已完成", cancelled: "已取消", chatting: "正在回复" },
  en: { project: "This project", all: "All projects", search: "Search titles, objectives or projects", empty: "No build conversations in this project", noMatch: "No matching conversations",
    new: "New conversation in this project", more: "View all", title: "Build conversations", close: "Close history", current: "Current conversation", unfiled: "No project",
    draft: "No task yet", ready: "Ready to start", running: "Running", paused: "Paused", needs_you: "Needs you", completed: "Completed", cancelled: "Cancelled", chatting: "Replying" },
};
const normalize = (value = "") => value.replace(/\\/g, "/").replace(/\/$/, "").toLocaleLowerCase();
export function sameHistoryProject(a: BuildHistoryEntry, b: BuildHistoryEntry) {
  if (a.projectId && b.projectId) return a.projectId === b.projectId;
  return normalize(a.workspace) === normalize(b.workspace);
}

export function BuildHistory({ entries, lang, busy, error, workspace, onOpen, onNew }: {
  entries: BuildHistoryEntry[]; lang: "zh" | "en"; busy?: boolean; error?: string; workspace?: string;
  onOpen?(id: string): Promise<unknown> | void; onNew?(): void;
}) {
  const text = copy[lang];
  const [all, setAll] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [opening, setOpening] = useState("");
  const [openError, setOpenError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
  }, [expanded]);
  const current = entries.find(entry => entry.current);
  const scope = current || { workspace } as BuildHistoryEntry;
  const scoped = entries.filter(entry => all || sameHistoryProject(entry, scope));
  const needle = query.trim().toLocaleLowerCase();
  const matches = scoped.filter(entry => !needle || [entry.title, entry.objective, entry.projectName, entry.workspace]
    .some(value => value?.toLocaleLowerCase().includes(needle)));
  const recent = matches.slice(0, 5);
  if (!needle && current && matches.includes(current)) {
    recent.splice(0, recent.length, current, ...matches.filter(entry => entry !== current).slice(0, 4));
  }
  const open = async (id: string) => {
    if (pending.current || !onOpen) return;
    pending.current = true; setOpening(id); setOpenError("");
    try { await onOpen(id); if (mounted.current) setExpanded(false); }
    catch { if (mounted.current) setOpenError(lang === "zh" ? "打开失败，请重试" : "Could not open. Try again."); }
    finally { pending.current = false; if (mounted.current) setOpening(""); }
  };
  const controls = () => <div className="bh-controls">
    <div className="bh-scopes" role="group" aria-label={text.title}>
      <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>{text.project}</button>
      <button type="button" aria-pressed={all} onClick={() => setAll(true)}>{text.all}</button>
      <span>{matches.length}{needle ? ` / ${scoped.length}` : ""}</span>
    </div>
    <label className="bh-search"><Search size={13} /><input type="search" aria-label={text.search} placeholder={text.search}
      value={query} onChange={event => setQuery(event.target.value)} /></label>
  </div>;
  const rows = (values: BuildHistoryEntry[]) => <div className="bh-list">
    {values.map(entry => {
      const project = entry.projectName || entry.workspace?.split(/[\\/]/).filter(Boolean).pop() || text.unfiled;
      return <button key={entry.id} type="button" className="pf-well bh-row" aria-current={entry.current ? "page" : undefined}
        aria-busy={opening === entry.id || undefined} disabled={entry.current || !!opening || !onOpen}
        title={[entry.title, entry.workspace, entry.objective].filter(Boolean).join("\n")} onClick={() => void open(entry.id)}>
        <span className="bh-top"><strong>{entry.title || (lang === "zh" ? "新建造对话" : "New build conversation")}</strong>
          {entry.current ? <span className="bh-current">{text.current}</span> : <time>{entry.when}</time>}</span>
        <span className="bh-project">{project}</span>
        <span className="bh-bottom"><span data-state={entry.status || "draft"}>{text[entry.status || "draft"]}</span>
          {!!entry.total && <span>{entry.milestone ? (lang === "zh" ? `阶段 ${entry.milestone} · ` : `Stage ${entry.milestone} · `) : ""}{entry.done || 0}/{entry.total}{lang === "zh" ? " 项任务" : " tasks"}</span>}
          {entry.live && <span className="bh-chatting">{text.chatting}</span>}</span>
      </button>;
    })}
    {!values.length && <p className="pf-empty" role="status">{needle ? text.noMatch : text.empty}</p>}
  </div>;
  return <div className="bh-root">
    {controls()}
    {(error || openError) && <p className="bh-error" role="status">{openError || error}</p>}
    {rows(recent)}
    <button type="button" className="bh-more" onClick={() => setExpanded(true)}>{text.more} ({scoped.length})</button>
    {onNew && <button type="button" className="pf-add" disabled={busy || !!opening} onClick={onNew}><Plus size={12} />{text.new}</button>}
    {expanded && <dialog ref={dialog} className="bh-dialog" aria-label={text.title} onCancel={() => setExpanded(false)}
      onClick={event => { if (event.target === dialog.current) { const box = dialog.current.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) setExpanded(false); } }}>
      <header><h2>{text.title}</h2><button type="button" autoFocus aria-label={text.close} onClick={() => setExpanded(false)}><X size={18} /></button></header>
      {controls()}{(error || openError) && <p className="bh-error" role="status">{openError || error}</p>}{rows(matches)}
      <footer>{onNew && <button type="button" className="pf-add" disabled={busy || !!opening} onClick={() => { setExpanded(false); onNew(); }}><Plus size={12} />{text.new}</button>}</footer>
    </dialog>}
  </div>;
}

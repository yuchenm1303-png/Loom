import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Archive, Check, Pencil, Quill, Search, Trash2, X } from "./icons";
import "./build-history.css";

export interface BuildHistoryEntry {
  id: string; title: string; when: string; current: boolean; live: boolean;
  workspace?: string; projectId?: string; projectName?: string; objective?: string;
  status?: "draft" | "ready" | "running" | "paused" | "needs_you" | "completed" | "cancelled";
  done?: number; total?: number; milestone?: number;
}

const copy = {
  zh: { project: "当前项目", all: "全部项目", search: "搜索标题、目标或项目", clear: "清除搜索", empty: "此项目还没有建造对话", noMatch: "没有匹配的对话",
    new: "在当前项目新建对话", newShort: "新建", more: "查看全部", title: "建造对话", close: "关闭记录", current: "当前", unfiled: "未绑定项目",
    rename: "重命名", archive: "归档", delete: "删除", deleteConfirm: "再点一次删除", save: "保存", cancel: "取消",
    keepCurrent: "当前对话不能归档或删除", keepLive: "正在回复，结束后才能归档或删除",
    openFailed: "打开失败，请重试", actionFailed: "操作失败，请重试", untitled: "新建造对话", tasks: " 项任务", stage: (n: number) => `阶段 ${n} · `,
    draft: "未开始", ready: "待启动", running: "执行中", paused: "已暂停", needs_you: "待你处理", completed: "已完成", cancelled: "已取消", chatting: "正在回复" },
  en: { project: "This project", all: "All projects", search: "Search titles, objectives or projects", clear: "Clear search", empty: "No build conversations in this project", noMatch: "No matching conversations",
    new: "New conversation in this project", newShort: "New", more: "View all", title: "Build conversations", close: "Close history", current: "Current", unfiled: "No project",
    rename: "Rename", archive: "Archive", delete: "Delete", deleteConfirm: "Click again to delete", save: "Save", cancel: "Cancel",
    keepCurrent: "The current conversation can't be archived or deleted", keepLive: "Replying; archive or delete it once it finishes",
    openFailed: "Could not open. Try again.", actionFailed: "That didn't work. Try again.", untitled: "New build conversation", tasks: " tasks", stage: (n: number) => `Stage ${n} · `,
    draft: "Not started", ready: "Ready to start", running: "Running", paused: "Paused", needs_you: "Needs you", completed: "Completed", cancelled: "Cancelled", chatting: "Replying" },
};
const normalize = (value = "") => value.replace(/\\/g, "/").replace(/\/$/, "").toLocaleLowerCase();
export function sameHistoryProject(a: BuildHistoryEntry, b: BuildHistoryEntry) {
  if (a.projectId && b.projectId) return a.projectId === b.projectId;
  return normalize(a.workspace) === normalize(b.workspace);
}

/** The search words lit where they occur in a title. */
function marked(text: string, needle: string): ReactNode {
  if (!needle) return text;
  const lower = text.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, from)) {
    parts.push(<Fragment key={from}>{text.slice(from, at)}</Fragment>, <mark key={`m${at}`}>{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(<Fragment key={from}>{text.slice(from)}</Fragment>);
  return parts;
}

export function BuildHistory({ entries, lang, busy, error, workspace, onOpen, onNew, onRename, onArchive, onDelete }: {
  entries: BuildHistoryEntry[]; lang: "zh" | "en"; busy?: boolean; error?: string; workspace?: string;
  onOpen?(id: string): Promise<unknown> | void; onNew?(): void;
  onRename?(id: string, title: string): Promise<unknown> | void;
  onArchive?(id: string): Promise<unknown> | void;
  onDelete?(id: string): Promise<unknown> | void;
}) {
  const text = copy[lang];
  const [all, setAll] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [opening, setOpening] = useState("");
  const [working, setWorking] = useState("");
  const [renaming, setRenaming] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [armed, setArmed] = useState("");
  // Archived or deleted here: hidden at once, before the list from the Host catches up.
  const [gone, setGone] = useState<string[]>([]);
  const [actionError, setActionError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
  }, [expanded]);
  // An armed delete disarms itself: a second click has to follow the first, not come back to it minutes later.
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(""), 3200);
    return () => window.clearTimeout(timer);
  }, [armed]);
  useEffect(() => {
    setGone(ids => { const kept = ids.filter(id => entries.some(entry => entry.id === id)); return kept.length === ids.length ? ids : kept; });
  }, [entries]);
  const shown = gone.length ? entries.filter(entry => !gone.includes(entry.id)) : entries;
  const current = shown.find(entry => entry.current);
  const scope = current || { workspace } as BuildHistoryEntry;
  const inProject = shown.filter(entry => sameHistoryProject(entry, scope));
  const scoped = all ? shown : inProject;
  const needle = query.trim().toLocaleLowerCase();
  const matches = scoped.filter(entry => !needle || [entry.title, entry.objective, entry.projectName, entry.workspace]
    .some(value => value?.toLocaleLowerCase().includes(needle)));
  const recent = matches.slice(0, 5);
  if (!needle && current && matches.includes(current)) {
    recent.splice(0, recent.length, current, ...matches.filter(entry => entry !== current).slice(0, 4));
  }
  const errorText = actionError || error;
  const open = async (id: string) => {
    if (pending.current || !onOpen) return;
    pending.current = true; setOpening(id); setActionError("");
    // Opened, the search has done its work (the app opens the conversation in a new hub anyway).
    try { await onOpen(id); if (mounted.current) { setExpanded(false); setQuery(""); } }
    catch { if (mounted.current) setActionError(text.openFailed); }
    finally { pending.current = false; if (mounted.current) setOpening(""); }
  };
  const run = async (id: string, job: () => Promise<unknown> | void, hide = false) => {
    if (pending.current) return;
    pending.current = true; setWorking(id); setActionError(""); setArmed("");
    try {
      await job();
      if (mounted.current && hide) setGone(ids => [...ids, id]);
    } catch { if (mounted.current) setActionError(text.actionFailed); }
    finally { pending.current = false; if (mounted.current) setWorking(""); }
  };
  const startRename = (entry: BuildHistoryEntry) => { setArmed(""); setRenaming(entry.id); setDraftTitle(entry.title); };
  const saveRename = (entry: BuildHistoryEntry) => {
    const title = draftTitle.trim();
    setRenaming("");
    if (!title || title === entry.title || !onRename) return;
    void run(entry.id, () => onRename(entry.id, title));
  };

  const controls = (dialogMode: boolean) => <div className="bh-controls">
    <div className="bh-scopes" role="group" aria-label={text.title}>
      <button type="button" aria-pressed={!all} onClick={() => setAll(false)} aria-label={text.project}>
        {text.project}<span className="bh-count" aria-hidden="true">{inProject.length}</span></button>
      <button type="button" aria-pressed={all} onClick={() => setAll(true)} aria-label={text.all}>
        {text.all}<span className="bh-count" aria-hidden="true">{shown.length}</span></button>
    </div>
    <label className="bh-search"><Search size={13} aria-hidden="true" />
      <input type="search" aria-label={text.search} placeholder={text.search} value={query} autoFocus={dialogMode && !!query}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => { if (event.key === "Escape" && query) { event.preventDefault(); event.stopPropagation(); setQuery(""); } }} />
      {needle ? <span className="bh-hits" aria-live="polite">{matches.length}</span> : null}
      {query ? <button type="button" className="bh-clear" aria-label={text.clear} title={text.clear} onClick={() => setQuery("")}><X size={12} /></button> : null}
    </label>
  </div>;

  const row = (entry: BuildHistoryEntry) => {
    const project = entry.projectName || entry.workspace?.split(/[\\/]/).filter(Boolean).pop() || text.unfiled;
    const state = entry.status || "draft";
    const title = entry.title || text.untitled;
    const locked = entry.current ? text.keepCurrent : entry.live ? text.keepLive : "";
    const isArmed = armed === entry.id;
    const rowBusy = working === entry.id || opening === entry.id;
    if (renaming === entry.id) {
      return <form key={entry.id} className="pf-well bh-row bh-rename" data-current={entry.current || undefined}
        onSubmit={event => { event.preventDefault(); saveRename(entry); }}>
        <input aria-label={text.rename} value={draftTitle} autoFocus maxLength={200} onFocus={event => event.currentTarget.select()}
          onChange={event => setDraftTitle(event.target.value)}
          onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setRenaming(""); } }}
          onBlur={() => saveRename(entry)} />
        <button type="submit" aria-label={text.save} title={text.save} onMouseDown={event => event.preventDefault()}><Check size={13} /></button>
        <button type="button" aria-label={text.cancel} title={text.cancel} onMouseDown={event => event.preventDefault()} onClick={() => setRenaming("")}><X size={13} /></button>
      </form>;
    }
    return <div key={entry.id} className="pf-well bh-row" data-current={entry.current || undefined} data-armed={isArmed || undefined}
      aria-busy={rowBusy || undefined} onPointerLeave={() => isArmed && setArmed("")}>
      <button type="button" className="bh-open" aria-current={entry.current ? "page" : undefined}
        disabled={entry.current || !!opening || !!working || !onOpen}
        title={[title, entry.workspace, entry.objective].filter(Boolean).join("\n")} onClick={() => void open(entry.id)}>
        <span className="bh-top"><strong>{marked(title, needle)}</strong>
          {entry.current ? <span className="bh-current">{text.current}</span> : <time>{entry.when}</time>}</span>
        <span className="bh-bottom">
          <span className="bh-state" data-state={entry.live ? "live" : state}>{entry.live ? text.chatting : text[state]}</span>
          {!!entry.total && <span>{entry.milestone ? text.stage(entry.milestone) : ""}{entry.done || 0}/{entry.total}{text.tasks}</span>}
          {(all || needle) && <span className="bh-project">{needle ? marked(project, needle) : project}</span>}
        </span>
      </button>
      {(onRename || onArchive || onDelete) && <span className="bh-actions" role="group" aria-label={title}>
        {onRename && <button type="button" aria-label={`${text.rename} ${title}`} title={text.rename} disabled={!!working}
          onClick={() => startRename(entry)}><Pencil size={12} /></button>}
        {onArchive && <button type="button" aria-label={`${text.archive} ${title}`} title={locked || text.archive} disabled={!!locked || !!working}
          onClick={() => void run(entry.id, () => onArchive(entry.id), true)}><Archive size={12} /></button>}
        {onDelete && <button type="button" className="bh-delete" data-armed={isArmed || undefined}
          aria-label={isArmed ? `${text.deleteConfirm}: ${title}` : `${text.delete} ${title}`} title={locked || (isArmed ? text.deleteConfirm : text.delete)}
          disabled={!!locked || !!working}
          onClick={() => isArmed ? void run(entry.id, () => onDelete(entry.id), true) : setArmed(entry.id)}>
          <Trash2 size={12} />{isArmed ? <span>{text.deleteConfirm}</span> : null}</button>}
      </span>}
    </div>;
  };
  const rows = (values: BuildHistoryEntry[]) => <div className="bh-list">
    {values.map(row)}
    {!values.length && <p className="pf-empty bh-empty" role="status">{needle ? text.noMatch : text.empty}</p>}
  </div>;
  const newButton = (onClick: () => void) => onNew && <button type="button" className="bh-new" aria-label={text.new} title={text.new}
    disabled={busy || !!opening} onClick={onClick}><Quill size={14} strokeWidth={2.1} aria-hidden="true" /><span>{text.newShort}</span></button>;
  return <div className="bh-root">
    {controls(false)}
    {errorText && <p className="bh-error" role="status">{errorText}</p>}
    {rows(recent)}
    {(onNew || matches.length > recent.length) && <div className="bh-foot">
      {newButton(() => onNew?.())}
      {matches.length > recent.length && <button type="button" className="bh-more" onClick={() => setExpanded(true)}>{text.more} ({matches.length})</button>}
    </div>}
    {expanded && <dialog ref={dialog} className="bh-dialog" aria-label={text.title} onCancel={() => { setRenaming(""); setExpanded(false); }}
      onClick={event => { if (event.target === dialog.current) { const box = dialog.current.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) setExpanded(false); } }}>
      <header><h2>{text.title}</h2><button type="button" className="bh-close" autoFocus aria-label={text.close} onClick={() => setExpanded(false)}><X size={18} /></button></header>
      {controls(true)}{errorText && <p className="bh-error" role="status">{errorText}</p>}{rows(matches)}
      {onNew && <footer>{newButton(() => { setExpanded(false); onNew(); })}</footer>}
    </dialog>}
  </div>;
}

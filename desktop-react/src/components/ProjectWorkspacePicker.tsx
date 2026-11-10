import { useCallback, useEffect, createContext, useContext, useId, useRef, useState, type KeyboardEvent } from "react";
import { Roll } from "./RollText";
import { createPortal } from "react-dom";
import { Check, ChevronLeft, FolderOpen, FolderSearch, Lock, X } from "./icons";
import type { ProjectRecord } from "../types/loom";

export interface WorkspaceState {
  path?: string; status?: "ready" | "not_git" | "unborn" | "detached" | "nested" | "invalid" | "unavailable";
  repositoryRoot?: string;
}

/** A path as the person reads it: their home folder becomes "~", the rest stays whole (the full path is the title). */
export function shortPath(path = "") {
  return path.replace(/^[a-z]:[\\/]users[\\/][^\\/]+(?=[\\/]|$)/i, "~");
}

// The host belongs to the canvas: project choices grow beside their repository card.
export const WorkspaceBranchContext = createContext<{
  target: HTMLDivElement | null; setOpen(open: boolean): void;
} | null>(null);

const normalize = (path = "") => path.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();

/** Existing projects grow into a child branch, followed by browsing for another folder. */
export function ProjectWorkspacePicker({ projects, workspace, locked, lang, branch, onSelect, onBrowse, onPrepare }: {
  projects: ProjectRecord[]; workspace?: WorkspaceState; locked: boolean; lang: "zh" | "en"; branch: string;
  onSelect?(id: string): void; onBrowse?(): void; onPrepare?(action: "initialize" | "branch"): void;
}) {
  const zh = lang === "zh";
  const selected = projects.find(item => normalize(item.root) === normalize(workspace?.path))?.id || "";
  const status = workspace?.status;
  const initialize = status === "not_git" || status === "unborn";
  const usable = !locked && Boolean(onSelect || onBrowse);
  return <div className="pf-workspace-controls">
    <ProjectSwitch projects={projects} selected={selected} zh={zh} disabled={!usable}
      lockedNote={locked ? (zh ? "工作期间目录保持不变；其他项目请新建会话" : "The folder stays fixed while working; use a new conversation for another project") : undefined}
      onSelect={onSelect} onBrowse={onBrowse} />
    {!locked && (initialize || status === "detached") ? <details className="pf-workspace-setup" key={`${workspace?.path}:${status}`}>
      <summary>{initialize ? (zh ? "准备 Git 仓库" : "Prepare Git repository") : (zh ? "创建工作分支" : "Create work branch")}</summary>
      <p>{initialize
        ? (zh ? "将创建本地 Git 仓库和一个空的初始提交。现有文件不会自动提交或上传；已有代码需查看并提交后才会被建造任务使用。" : "Create local Git history with an empty initial commit. Existing files are not committed or uploaded; review and commit them before building on them.")
        : (zh ? `将在当前提交上创建 ${branch}，保留现有文件，不切换到其他版本。` : `Create ${branch} at the current commit, preserving existing files.`)}</p>
      <button type="button" className="pf-setup-button" onClick={() => onPrepare?.(initialize ? "initialize" : "branch")} disabled={!onPrepare}>
        {initialize ? (zh ? "确认初始化（不提交现有文件）" : "Initialize (exclude existing files)") : (zh ? "确认创建分支" : "Create this branch")}
      </button>
    </details> : null}
    {!locked && status === "nested" ? <p className="pf-workspace-note">{zh ? `请选择 Git 根目录：${workspace?.repositoryRoot}` : `Choose the Git root: ${workspace?.repositoryRoot}`}</p> : null}
    {!locked && (status === "invalid" || status === "unavailable") ? <p className="pf-workspace-note">{zh ? "目录不可用或 Git 信息损坏，请重新选择文件夹。" : "Folder unavailable or Git metadata invalid; choose another folder."}</p> : null}
  </div>;
}

type Entry = { kind: "project"; project: ProjectRecord } | { kind: "browse" };

function ProjectSwitch({ projects, selected, zh, disabled, lockedNote, onSelect, onBrowse }: {
  projects: ProjectRecord[]; selected: string; zh: boolean; disabled: boolean; lockedNote?: string;
  onSelect?(id: string): void; onBrowse?(): void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const branch = useContext(WorkspaceBranchContext);
  const listId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const entries: Entry[] = [
    ...(onSelect ? projects.map(project => ({ kind: "project" as const, project })) : []),
    ...(onBrowse ? [{ kind: "browse" as const }] : []),
  ];

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }, []);

  useEffect(() => {
    branch?.setOpen(open);
    return () => branch?.setOpen(false);
  }, [open, branch?.setOpen]);

  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !trigger.current?.contains(target)) close(false);
    };
    document.addEventListener("pointerdown", away, true);
    return () => {
      document.removeEventListener("pointerdown", away, true);
    };
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => {
      const list = menu.current?.querySelector<HTMLElement>(".pf-switch-menu");
      const row = list?.querySelector<HTMLElement>(`[data-index="${active}"]`);
      if (!list || !row) return;
      const offset = row.getBoundingClientRect().top - list.getBoundingClientRect().top;
      if (offset < 0) list.scrollTop += offset;
      else if (offset + row.offsetHeight > list.clientHeight) list.scrollTop += offset + row.offsetHeight - list.clientHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [open, active]);

  const choose = (entry: Entry | undefined) => {
    if (!entry) return;
    close(true);
    if (entry.kind === "browse") onBrowse?.();
    else if (entry.project.id !== selected) onSelect?.(entry.project.id);
  };

  const show = () => {
    const at = entries.findIndex(entry => entry.kind === "project" && entry.project.id === selected);
    setActive(at < 0 ? 0 : at);
    setOpen(true);
  };

  const keys = (event: KeyboardEvent) => {
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); }
      return;
    }
    const last = entries.length - 1;
    if (event.key === "ArrowDown") { event.preventDefault(); setActive(at => at >= last ? 0 : at + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActive(at => at <= 0 ? last : at - 1); }
    else if (event.key === "Home") { event.preventDefault(); setActive(0); }
    else if (event.key === "End") { event.preventDefault(); setActive(last); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(entries[active]); }
    else if (event.key === "Escape") { event.preventDefault(); close(true); }
    else if (event.key === "Tab") close(false);
  };

  const label = zh ? "切换项目" : "Switch project";
  return <>
    <button ref={trigger} type="button" className="pf-switch" disabled={disabled} data-open={open || undefined}
            aria-haspopup="listbox" aria-controls={open ? listId : undefined} aria-expanded={open} aria-label={zh ? "选择项目或文件夹" : "Choose a project or folder"}
            aria-activedescendant={open ? `${listId}-${active}` : undefined}
            title={lockedNote} onClick={() => (open ? close(false) : show())} onKeyDown={keys}>
      {lockedNote ? <Lock size={13} /> : <FolderOpen size={14} />}
      <span><Roll text={lockedNote ? (zh ? "项目已锁定" : "Project locked") : label} /></span>
      {lockedNote ? null : <ChevronLeft size={14} className="pf-switch-chevron" />}
    </button>
    {open ? (() => {
      const panel = <div ref={menu} className="pf-workspace-branch" onKeyDown={keys}>
        <header className="pf-workspace-branch-head">
          <FolderOpen size={13} /><span>{zh ? "已有项目" : "Projects"}</span>
          <button type="button" onClick={() => close(true)} onKeyDown={event => { if (event.key !== "Escape") event.stopPropagation(); }} aria-label={zh ? "收起项目支路" : "Close project branch"}><X size={13} /></button>
        </header>
        <div id={listId} className="pf-switch-menu" role="listbox" aria-label={label}
             onMouseDown={event => event.preventDefault()}>
        {entries.map((entry, index) => entry.kind === "project"
          ? <div key={entry.project.id} id={`${listId}-${index}`} data-index={index} role="option"
                 aria-selected={entry.project.id === selected} data-active={index === active || undefined}
                 className="pf-switch-option" title={entry.project.root}
                 onMouseMove={() => setActive(index)} onClick={() => choose(entry)}>
              <span className="pf-switch-mark"><FolderOpen size={13} /></span>
              <span className="pf-switch-text">
                <span className="pf-switch-name">{entry.project.name}</span>
                <span className="pf-switch-path">{shortPath(entry.project.root)}</span>
              </span>
              {entry.project.id === selected ? <Check size={14} className="pf-switch-check" /> : null}
            </div>
          : <div key="browse" id={`${listId}-${index}`} data-index={index} role="option" aria-selected={false}
                 data-active={index === active || undefined} className="pf-switch-option pf-switch-browse"
                 onMouseMove={() => setActive(index)} onClick={() => choose(entry)}>
              <span className="pf-switch-mark"><FolderSearch size={13} /></span>
              <span className="pf-switch-text"><span className="pf-switch-name">{zh ? "选择其他文件夹…" : "Choose another folder…"}</span></span>
            </div>)}
        </div>
      </div>;
      return branch?.target ? createPortal(panel, branch.target) : panel;
    })() : null}
  </>;
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ChevronDown, ChevronRight, File, FileText, Folder, FolderOpen,
  Maximize2, Pencil, Plus, RefreshCw, Save, Search, Trash2, X,
} from "./icons";
import type { MotionPresencePhase } from "../motion/useMotionPresence";
import { PanelEmpty } from "./PanelEmpty";
import { PanelHeader } from "./PanelHeader";
import { withProjectReadTimeout } from "../state/projectRead";
import { useI18n } from "../i18n";
import "./files-workbench.css";

type Entry = { path: string; name: string; type: string; size?: number };
type ListResult = { entries: Entry[]; nextOffset: number | null };
type ReadResult = { path: string; text: string | null; sha?: string; size?: number; reason?: string };
type SearchResult = { entries: Entry[]; truncated: boolean };
type OpenFile = { baseline: string; text: string; sha: string; reason?: string };
type Action = "list" | "search" | "read" | "write" | "mkdir" | "rename" | "delete";

interface FilesWorkbenchProps {
  projectId: string;
  projectName: string;
  root: string;
  open: boolean;
  /** Presence phase from the owner, which keeps the workbench mounted while it leaves (App.tsx). */
  phase?: MotionPresencePhase;
  onClose(): void;
  onChanged?(): void;
}

function pathParent(path: string): string {
  return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}
function pathName(path: string): string {
  return path.split("/").pop() || path;
}
function displaySize(value?: number): string {
  if (value == null) return "";
  if (value < 1024) return String(value) + " B";
  return value < 1024 * 1024 ? (value / 1024).toFixed(1) + " KB" : (value / (1024 * 1024)).toFixed(1) + " MB";
}
function errorText(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}
function validName(value: string): boolean {
  return Boolean(value && value !== "." && value !== ".." && !/[\\/:*?"<>|\x00-\x1f]/.test(value));
}
function fileTreePath(entry: Entry): string {
  return entry.path;
}

export function FilesWorkbench({ projectId, projectName, root, open, phase = "entered", onClose, onChanged }: FilesWorkbenchProps) {
  const zh = useI18n().language === "zh-CN";
  const L = (chinese: string, english: string) => (zh ? chinese : english);
  const [directories, setDirectories] = useState<Record<string, Entry[]>>({});
  const [nextOffsets, setNextOffsets] = useState<Record<string, number | null>>({});
  const [expanded, setExpanded] = useState<string[]>([]);
  const [tabs, setTabs] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [focusedDirectory, setFocusedDirectory] = useState("");
  const [files, setFiles] = useState<Record<string, OpenFile>>({});
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Entry[]>([]);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const [pending, setPending] = useState("");
  const [loadingDirs, setLoadingDirs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [newKind, setNewKind] = useState<"file" | "folder" | "">("");
  const [newName, setNewName] = useState("");
  const [renamePath, setRenamePath] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [fullScreen, setFullScreen] = useState(false);
  const searchSerial = useRef(0);

  const call = useCallback(async <T,>(action: Action, params: Record<string, unknown>): Promise<T> => {
    if (!window.loom?.call) throw new Error("Loom Host is disconnected. Reconnect to manage local files.");
    const request = window.loom.call<T>("project/files", { projectId, action, ...params });
    return action === "list" || action === "search" || action === "read"
      ? withProjectReadTimeout(request)
      : request;
  }, [projectId]);

  const listDirectory = useCallback(async (path: string, offset = 0) => {
    setLoadingDirs((prev) => prev.includes(path) ? prev : [...prev, path]);
    try {
      const result = await call<ListResult>("list", { path, offset, showIgnored });
      setDirectories((prev) => ({
        ...prev,
        [path]: offset ? [...(prev[path] || []), ...result.entries] : result.entries,
      }));
      setNextOffsets((prev) => ({ ...prev, [path]: result.nextOffset }));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoadingDirs((prev) => prev.filter((item) => item !== path));
    }
  }, [call, showIgnored]);

  useEffect(() => {
    if (!open) return;
    setDirectories({});
    setNextOffsets({});
    setExpanded([]);
    setError("");
    void listDirectory("");
  }, [open, projectId, showIgnored, listDirectory]);

  useEffect(() => {
    if (!open) return;
    const term = query.trim();
    const serial = ++searchSerial.current;
    if (!term) {
      setResults([]);
      setSearching(false);
      setSearchTruncated(false);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      void call<SearchResult>("search", { path: "", query: term, showIgnored })
        .then((response) => {
          if (serial !== searchSerial.current) return;
          setResults(response.entries);
          setSearchTruncated(response.truncated);
        })
        .catch((cause) => {
          if (serial === searchSerial.current) setError(errorText(cause));
        })
        .finally(() => { if (serial === searchSerial.current) setSearching(false); });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [call, open, query, showIgnored]);

  const openFile = useCallback(async (path: string, reload = false) => {
    setTabs((prev) => prev.includes(path) ? prev : [...prev, path]);
    setSelected(path);
    if (!reload && files[path]) return;
    setPending("read");
    try {
      const result = await call<ReadResult>("read", { path });
      setFiles((prev) => ({
        ...prev,
        [path]: {
          baseline: result.text ?? "",
          text: result.text ?? "",
          sha: result.sha || "",
          reason: result.reason,
        },
      }));
      setError("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPending("");
    }
  }, [call, files]);

  const toggleFolder = useCallback((path: string) => {
    if (expanded.includes(path)) {
      setExpanded((prev) => prev.filter((item) => item !== path));
    } else {
      setExpanded((prev) => [...prev, path]);
      if (!Object.prototype.hasOwnProperty.call(directories, path)) void listDirectory(path);
    }
  }, [directories, expanded, listDirectory]);

  const treeRows = useMemo(() => {
    const rows: Array<{ entry: Entry; depth: number }> = [];
    const visit = (path: string, depth: number) => {
      for (const entry of directories[path] || []) {
        rows.push({ entry, depth });
        if (entry.type === "directory" && expanded.includes(entry.path)) visit(entry.path, depth + 1);
      }
    };
    visit("", 0);
    return rows;
  }, [directories, expanded]);

  const dirtyPaths = tabs.filter((path) => files[path] && files[path].text !== files[path].baseline);
  const current = selected ? files[selected] : undefined;
  const dirty = Boolean(current && current.text !== current.baseline);

  const requestClose = () => {
    if (dirtyPaths.length && !window.confirm(L("还有未保存的文件。确定关闭并放弃更改吗？", "Some files are not saved. Close and discard the changes?"))) return;
    onClose();
  };

  const refresh = async () => {
    setError("");
    await Promise.all(["", ...expanded].map((path) => listDirectory(path)));
    if (selected && !dirty && current && !current.reason) void openFile(selected, true);
  };

  const runMutation = async (action: Action, params: Record<string, unknown>, message: string) => {
    setPending(action);
    setError("");
    try {
      const value = await call<{ path: string }>(action, params);
      setNotice(message);
      setNewKind("");
      setRenamePath("");
      onChanged?.();
      await Promise.all(["", ...expanded].map((path) => listDirectory(path)));
      return value;
    } catch (cause) {
      setError(errorText(cause));
      return null;
    } finally {
      setPending("");
    }
  };

  const saveFile = async () => {
    if (!selected || !current || current.reason || !dirty) return;
    setPending("write");
    setError("");
    try {
      const result = await call<{ sha: string }>("write", { path: selected, text: current.text, expectedSha: current.sha });
      setFiles((prev) => {
        const existing = prev[selected];
        if (!existing) return prev;
        return { ...prev, [selected]: { ...existing, baseline: current.text, sha: result.sha } };
      });
      setNotice(L("文件已保存", "Saved"));
      onChanged?.();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPending("");
    }
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (renamePath) setRenamePath("");
        else if (newKind) setNewKind("");
        else requestClose();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveFile();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const closeTab = (path: string) => {
    if (files[path] && files[path].text !== files[path].baseline && !window.confirm(zh ? "关闭并放弃 " + pathName(path) + " 的未保存更改？" : "Close " + pathName(path) + " and discard its unsaved changes?")) return;
    const next = tabs.filter((item) => item !== path);
    setTabs(next);
    setFiles((prev) => { const copy = { ...prev }; delete copy[path]; return copy; });
    if (selected === path) setSelected(next[next.length - 1] || "");
  };

  const createNew = async () => {
    const name = newName.trim();
    if (!validName(name)) { setError(L("名称不能包含路径分隔符或特殊字符。", "A name cannot contain path separators or special characters.")); return; }
    const dir = focusedDirectory || (selected ? pathParent(selected) : "");
    const path = dir ? dir + "/" + name : name;
    if (newKind === "folder") {
      await runMutation("mkdir", { path }, L("文件夹已创建", "Folder created"));
    } else {
      const result = await runMutation("write", { path, text: "", expectedSha: "" }, L("文件已创建", "File created"));
      if (result) void openFile(path, true);
    }
    setNewName("");
  };

  const renameSelected = async () => {
    const name = renameValue.trim();
    if (!validName(name) || !renamePath) { setError(L("请输入有效的新名称。", "Enter a valid new name.")); return; }
    const parent = pathParent(renamePath);
    const newPath = parent ? parent + "/" + name : name;
    const result = await runMutation("rename", { path: renamePath, newPath }, L("已重命名", "Renamed"));
    if (!result) return;
    // If a folder was moved, its already-open children are no longer valid.
    setExpanded([]);
    setDirectories({});
    setTabs((prev) => prev.map((item) => item === renamePath ? newPath : item).filter((item) => !item.startsWith(renamePath + "/")));
    setFiles((prev) => {
      const copy = { ...prev };
      if (copy[renamePath]) { copy[newPath] = copy[renamePath]; delete copy[renamePath]; }
      for (const key of Object.keys(copy)) if (key.startsWith(renamePath + "/")) delete copy[key];
      return copy;
    });
    if (selected === renamePath) setSelected(newPath);
    else if (selected.startsWith(renamePath + "/")) setSelected("");
    if (focusedDirectory === renamePath) setFocusedDirectory(newPath);
    else if (focusedDirectory.startsWith(renamePath + "/")) setFocusedDirectory("");
    void listDirectory("");
  };

  const deletePath = async (path: string) => {
    if (!path) return;
    if (!window.confirm(zh ? "永久删除 " + path + "？文件夹必须为空，此操作不可撤销。" : "Delete " + path + " for good? A folder must be empty; this cannot be undone.")) return;
    const result = await runMutation("delete", { path }, L("已删除", "Deleted"));
    if (!result) return;
    setTabs((prev) => prev.filter((item) => item !== path));
    setFiles((prev) => { const copy = { ...prev }; delete copy[path]; return copy; });
    if (selected === path) setSelected("");
    if (focusedDirectory === path) setFocusedDirectory("");
  };

  const selectEntry = (entry: Entry) => {
    if (entry.type === "directory") {
      setFocusedDirectory(entry.path);
      toggleFolder(fileTreePath(entry));
      return;
    }
    if (entry.type === "symlink") { setError(L("为安全起见，不打开符号链接。", "Symbolic links are not opened, for safety.")); return; }
    setFocusedDirectory(pathParent(entry.path));
    void openFile(fileTreePath(entry));
  };

  const renderedRows = query.trim()
    ? results.map((entry) => ({ entry, depth: 0 }))
    : treeRows;

  return createPortal(
    <div
      className="files-workbench-backdrop pl-scrim pl-reveal"
      data-motion-phase={phase}
      inert={!open}
      onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}
    >
      <section className={"files-workbench pl-modal" + (fullScreen ? " full-screen" : "")} role="dialog" aria-modal="true" aria-label={L("文件", "Files")}>
        <PanelHeader
          className="files-workbench-header"
          mark={<FolderOpen size={18} strokeWidth={1.8} />}
          markClassName="files-workbench-mark"
          title={L("文件", "Files")}
          subtitle={projectName + " · " + root}
          subtitleHint={root}
          actions={<>
            <button type="button" className="pl-icon-button" onClick={() => void refresh()} title={L("刷新目录", "Refresh")} aria-label={L("刷新目录", "Refresh")}><RefreshCw size={16} /></button>
            <button type="button" className="pl-icon-button" onClick={() => setFullScreen((value) => !value)} title={L("切换最大化", "Toggle full size")} aria-label={L("切换最大化", "Toggle full size")}><Maximize2 size={16} /></button>
          </>}
          closeLabel={L("关闭文件", "Close Files")}
          onClose={requestClose}
        />
        <div className="files-workbench-main">
          <aside className="files-workbench-tree" aria-label={L("项目文件", "Project files")}>
            <div className="files-workbench-search pl-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={L("筛选文件名…", "Filter by name…")} aria-label={L("搜索文件名", "Search file names")} /></div>
            <div className="files-workbench-tools">
              <button type="button" onClick={() => { setNewKind("file"); setNewName(""); }} title={L("新建文件", "New file")}><FileText size={15} /> {L("文件", "File")}</button>
              <button type="button" onClick={() => { setNewKind("folder"); setNewName(""); }} title={L("新建文件夹", "New folder")}><Plus size={15} /> {L("文件夹", "Folder")}</button>
              {focusedDirectory ? <>
                <button type="button" onClick={() => { setRenamePath(focusedDirectory); setRenameValue(pathName(focusedDirectory)); }} title={L("重命名选中文件夹", "Rename the selected folder")}><Pencil size={15} /> {L("重命名", "Rename")}</button>
                <button type="button" onClick={() => void deletePath(focusedDirectory)} title={L("删除空文件夹", "Delete the empty folder")}><Trash2 size={15} /> {L("删除", "Delete")}</button>
              </> : null}
              <label><input type="checkbox" checked={showIgnored} onChange={(event) => setShowIgnored(event.target.checked)} /> {L("显示缓存", "Show ignored")}</label>
            </div>
            {newKind ? <form className="files-workbench-inline-form" onSubmit={(event) => { event.preventDefault(); void createNew(); }}>
              <input autoFocus aria-label={L("新名称", "New name")} placeholder={newKind === "file" ? L("文件名", "File name") : L("文件夹名", "Folder name")} value={newName} onChange={(event) => setNewName(event.target.value)} />
              <button type="submit" disabled={!!pending}>{L("创建", "Create")}</button>
              <button type="button" onClick={() => setNewKind("")} aria-label={L("取消", "Cancel")}>×</button>
            </form> : null}
              {renamePath ? <form className="files-workbench-rename" onSubmit={(event) => { event.preventDefault(); void renameSelected(); }}>
                <input aria-label={L("重命名", "Rename")} autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} />
                <button type="submit" disabled={!!pending}>{L("确定", "OK")}</button>
                <button type="button" onClick={() => setRenamePath("")}>{L("取消", "Cancel")}</button>
              </form> : null}
            <div className="files-workbench-scroll" role="tree" aria-label={L("文件目录树", "File tree")}>
              {searching ? <p className="files-workbench-placeholder">{L("正在搜索…", "Searching…")}</p> : null}
              {renderedRows.map(({ entry, depth }) => {
                const isFolder = entry.type === "directory";
                const isOpen = expanded.includes(entry.path);
                const Icon = isFolder ? (isOpen ? FolderOpen : Folder) : File;
                return <div role="treeitem" aria-expanded={isFolder ? isOpen : undefined} aria-selected={selected === entry.path} key={entry.path}>
                  <button type="button" className={"files-workbench-row" + (selected === entry.path || (entry.type === "directory" && focusedDirectory === entry.path) ? " selected" : "")} onClick={() => selectEntry(entry)}
                    style={{ paddingLeft: (12 + depth * 18) + "px" }} title={entry.path}>
                    {isFolder ? isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span className="files-workbench-spacer" />}
                    <Icon size={16} strokeWidth={1.7} />
                    <span className="files-workbench-filename">{query.trim() ? entry.path : entry.name}</span>
                    <small>{isFolder ? "" : displaySize(entry.size)}</small>
                  </button>
                </div>;
              })}
              {!query.trim() && Object.entries(nextOffsets).filter(([, next]) => next !== null).map(([path, next]) => (
                <button className="files-workbench-more" type="button" key={path} disabled={loadingDirs.includes(path)} onClick={() => void listDirectory(path, next || 0)}>{L("加载更多", "Load more")} · {path || projectName}</button>
              ))}
              {!renderedRows.length && !searching && !loadingDirs.length ? <p className="files-workbench-placeholder">{error ? L("文件列表读取失败，请刷新重试。", "Could not read the files; refresh to try again.") : query ? L("没有匹配的文件", "No matching files") : !Object.prototype.hasOwnProperty.call(directories, "") ? L("正在读取目录…", "Reading the folder…") : L("当前目录为空", "This folder is empty")}</p> : null}
              {searchTruncated && query ? <p className="files-workbench-placeholder">{L("结果较多，请输入更具体的文件名", "Many results; try a more specific name")}</p> : null}
            </div>
          </aside>
          <main className="files-workbench-preview">
            <div className="files-workbench-tabs" role="tablist" aria-label={L("已打开的文件", "Open files")}>
              {tabs.map((path) => <div key={path} className={"files-workbench-tab" + (path === selected ? " active" : "")}>
                <button type="button" role="tab" aria-selected={path === selected} onClick={() => setSelected(path)} title={path}>{pathName(path)}{files[path]?.text !== files[path]?.baseline ? " ●" : ""}</button>
                <button type="button" aria-label={L("关闭 ", "Close ") + pathName(path)} title={L("关闭标签", "Close tab")} onClick={() => closeTab(path)}><X size={12} /></button>
              </div>)}
              {!tabs.length ? <span className="files-workbench-tab-hint">{L("选择文件以查看内容", "Choose a file to see it")}</span> : null}
            </div>
            {selected ? <>
              <div className="files-workbench-filebar">
                <span className="files-workbench-filepath" title={selected}>{selected}</span>
                <div className="files-workbench-file-actions">
                  <button type="button" onClick={() => { setRenamePath(selected); setRenameValue(pathName(selected)); }} title={L("重命名", "Rename")}><Pencil size={15} /></button>
                  <button type="button" onClick={() => void deletePath(selected)} title={L("删除文件", "Delete file")}><Trash2 size={15} /></button>
                  <button type="button" onClick={() => void openFile(selected, true)} disabled={dirty} title={dirty ? L("先保存或放弃更改", "Save or discard the changes first") : L("重新读取", "Read again")}><RefreshCw size={15} /></button>
                  <button type="button" className="files-workbench-save" onClick={() => void saveFile()} disabled={!dirty || !!pending}><Save size={15} /> {L("保存", "Save")}</button>
                </div>
              </div>
              
              {current?.reason ? <div className="files-workbench-unsupported"><File size={30} /><strong>{L("无法作为文本编辑", "Not editable as text")}</strong><span>{current.reason}</span><button type="button" onClick={() => void window.loom.openLocalArtifact(selected, root)}>{L("使用文件预览打开", "Open in the preview")}</button></div>
                : current ? <textarea className="files-workbench-editor" spellCheck={false} aria-label={L("编辑 ", "Edit ") + selected}
                  value={current.text} onChange={(event) => {
                    const value = event.target.value;
                    setFiles((prev) => ({ ...prev, [selected]: { ...prev[selected], text: value } }));
                  }} /> : <p className="files-workbench-placeholder">{pending === "read" ? L("正在读取文件…", "Reading the file…") : L("文件尚未读取成功，请重新读取。", "The file was not read; read it again.")}</p>}
              <div className="files-workbench-status"><span>{!current ? L("尚未加载", "Not loaded") : dirty ? L("● 有未保存的更改", "● Unsaved changes") : L("文件已同步", "Saved")}</span><span>UTF-8 · {displaySize(current ? new TextEncoder().encode(current.text).length : undefined)}</span></div>
            </> : <div className="files-workbench-empty"><PanelEmpty icon={<FolderOpen size={22} strokeWidth={1.7} />} title={L("打开文件以开始", "Open a file to begin")} copy={zh
                ? <>从左侧目录树选择文件，或搜索整个项目的文件名。<br />编辑内容后按 Ctrl+S 保存。</>
                : <>Choose a file in the tree, or search the whole project by name.<br />Press Ctrl+S to save an edit.</>} /></div>}
          </main>
        </div>
        {(error || notice) ? <footer className={"files-workbench-notice" + (error ? " is-error" : "")}><span>{error || notice}</span><button type="button" onClick={() => { setError(""); setNotice(""); }} aria-label={L("关闭消息", "Dismiss")}><X size={14} /></button></footer> : null}
      </section>
    </div>, document.body,
  );
}

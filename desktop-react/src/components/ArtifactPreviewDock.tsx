import { ArrowLeft, ExternalLink, FileCode2, RefreshCw, Search, Package, ChevronRight, Image, FileText, Music, Film, Globe, X } from "./icons";
import { useEffect, useMemo, useRef, useState, type ReactNode, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { artifactName, artifactRenderer, canRenderArtifact, normalizeArtifactPath } from "../artifactRenderers";
import type { TranscriptItem } from "../types/loom";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import { ArtifactRenderSurface } from "./ArtifactRenderSurface";
import { PanelHeader } from "./PanelHeader";
import { useLoomLanguage } from "../i18n";
import "./artifact-preview-dock.css";

interface ArtifactPreviewDockProps {
  items: TranscriptItem[];
  open: boolean;
  path: string;
  workspace: string;
  onClose(): void;
  /** Reuse the preview sidebar shell for another workspace surface. */
  content?: (visible: boolean) => ReactNode;
  ariaLabel?: string;
}

const ARTIFACT_DOCK_WIDTH_KEY = "loom.layout.artifactPreviewWidth";
const ARTIFACT_DOCK_DEFAULT = 620;
const artifactIcons = { web: Globe, image: Image, pdf: FileText, text: FileText, audio: Music, video: Film, code: FileCode2, data: FileCode2, file: FileText };

function previewBounds(): { min: number; max: number } {
  const viewport = Math.max(360, window.innerWidth || 0);
  if (viewport <= 760) {
    return {
      min: Math.min(320, viewport * 0.8),
      max: Math.max(320, viewport * 0.96),
    };
  }
  return {
    min: 420,
    max: Math.max(420, Math.min(820, viewport - 560)),
  };
}

function clampPreviewWidth(value: number): number {
  const { min, max } = previewBounds();
  return Math.round(Math.min(max, Math.max(min, value)));
}

function readPreviewWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(ARTIFACT_DOCK_WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampPreviewWidth(stored);
  } catch {
    // Keep the default when storage is unavailable.
  }
  return clampPreviewWidth(ARTIFACT_DOCK_DEFAULT);
}

function persistPreviewWidth(value: number): void {
  try {
    window.localStorage.setItem(ARTIFACT_DOCK_WIDTH_KEY, String(Math.round(value)));
  } catch {
    // Resizing still works for this session.
  }
}

export function ArtifactPreviewDock({
  items,
  open,
  path,
  workspace,
  onClose,
  content,
  ariaLabel: ariaLabelProp,
}: ArtifactPreviewDockProps) {
  const language = useLoomLanguage();
  const zh = language === "zh-CN";
  const ariaLabel = ariaLabelProp ?? (zh ? "产物侧栏" : "Artifacts");
  const presence = useMotionPresence(open, RETAIN.fast);
  const [width, setWidth] = useState(readPreviewWidth);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState(path);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  useEffect(() => { setSelected(path); setQuery(""); setKind("all"); }, [path, open, workspace]);
  const artifacts = useMemo(() => {
    const entries = new Map<string, { path: string; revision: number; running: boolean }>();
    for (const item of items) {
      if (item.type !== "file_edit") continue;
      for (const raw of item.paths ?? []) {
        const value = normalizeArtifactPath(raw);
        if (!value || !canRenderArtifact(value)) continue;
        const key = value.toLowerCase();
        const previous = entries.get(key);
        entries.delete(key);
        entries.set(key, { path: value, revision: (previous?.revision ?? 0) + 1,
          running: item.status === "running" });
      }
    }
    return [...entries.values()].reverse();
  }, [items]);
  const kindLabels = zh
    ? { web: "网页", image: "图片", pdf: "PDF", text: "文本", audio: "音频", video: "视频", code: "代码", data: "数据", file: "文件" }
    : { web: "Web", image: "Images", pdf: "PDF", text: "Text", audio: "Audio", video: "Video", code: "Code", data: "Data", file: "Files" };
  const availableKinds = [...new Set(artifacts.map(item => artifactRenderer(item.path).kind))];
  const visibleArtifacts = artifacts.filter(item =>
    item.path.toLowerCase().includes(query.trim().toLowerCase()) &&
    (kind === "all" || artifactRenderer(item.path).kind === kind));
  const directoryLabel = (value: string) => {
    const root = normalizeArtifactPath(workspace).replace(/\/$/, "");
    const relative = root && value.toLowerCase().startsWith(`${root.toLowerCase()}/`)
      ? value.slice(root.length + 1) : value;
    return relative.includes("/") ? relative.slice(0, relative.lastIndexOf("/")) : (zh ? "工作区根目录" : "Workspace root");
  };
  const selectedArtifact = artifacts.find(item => item.path === selected);
  const resizeRef = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
    currentWidth: number;
  } | null>(null);
  const name = useMemo(() => artifactName(selected), [selected]);
  const renderer = useMemo(() => artifactRenderer(selected), [selected]);

  useEffect(() => {
    if (!open) return;
    document.documentElement.style.setProperty("--artifact-preview-pane-width", `${width}px`);
    return () => {
      document.documentElement.style.removeProperty("--artifact-preview-pane-width");
    };
  }, [width, open]);

  useEffect(() => {
    const handleResize = () => setWidth((current) => clampPreviewWidth(current));
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      document.body.classList.remove("loom-artifact-dock-resizing");
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    document.documentElement.dataset.loomArtifactPreviewOpen = "true";
    return () => {
      window.removeEventListener("keydown", handleKey);
      delete document.documentElement.dataset.loomArtifactPreviewOpen;
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
    document.body.classList.add("loom-artifact-dock-resizing");
    event.preventDefault();
  };

  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    const next = clampPreviewWidth(session.startWidth + (session.startX - event.clientX));
    session.currentWidth = next;
    event.currentTarget.style.transform = `translateX(${session.startWidth - next}px)`;
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
    persistPreviewWidth(session.currentWidth);
    document.body.classList.remove("loom-artifact-dock-resizing");
  };

  const resizeWithKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const amount = event.shiftKey ? 40 : 14;
    const next = clampPreviewWidth(width + (event.key === "ArrowLeft" ? amount : -amount));
    setWidth(next);
    persistPreviewWidth(next);
  };

  const resetWidth = () => {
    const next = clampPreviewWidth(ARTIFACT_DOCK_DEFAULT);
    setWidth(next);
    try {
      window.localStorage.removeItem(ARTIFACT_DOCK_WIDTH_KEY);
    } catch {
      // Ignore persistence failures.
    }
  };

  if (!presence.mounted) return null;

  return createPortal(
    <aside
      className="artifact-preview-dock pl-dock"
      data-motion-phase={presence.phase}
      inert={!open}
      data-open={open ? "true" : "false"}
      aria-label={ariaLabel}
    >
      <div
        className="artifact-preview-resizer pl-resizer"
        role="separator"
        aria-label={content ? (zh ? `调整${ariaLabel}宽度` : `Resize ${ariaLabel}`) : (zh ? "调整预览宽度" : "Resize the preview")}
        aria-orientation="vertical"
        aria-valuemin={Math.round(previewBounds().min)}
        aria-valuemax={Math.round(previewBounds().max)}
        aria-valuenow={width}
        tabIndex={0}
        title={zh ? "拖动调整宽度 · 双击恢复默认" : "Drag to resize · double-click to reset"}
        onPointerDown={beginResize}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={resizeWithKeyboard}
        onDoubleClick={resetWidth}
      />

      {content ? content(open) : <>
      <PanelHeader
        className="artifact-preview-header"
        mark={selected
          ? <button type="button" className="artifact-back" aria-label={zh ? "返回产物列表" : "Back to artifacts"} onClick={() => setSelected("")}><ArrowLeft size={17} /></button>
          : <Package size={17} strokeWidth={1.8} />}
        markClassName="artifact-preview-mark"
        title={selected ? name : (zh ? `产物 · ${artifacts.length}` : `Artifacts · ${artifacts.length}`)}
        titleHint={selected ? name : undefined}
        subtitle={selected ? selected.replaceAll("\\", "/") : (zh ? "当前对话" : "This conversation")}
        subtitleCode={Boolean(selected)}
        subtitleHint={selected || undefined}
        actionsClassName="artifact-preview-actions"
        actions={selected ? <>
          <button type="button" className="pl-icon-button" onClick={() => setRevision((value) => value + 1)} title={zh ? "重新加载预览" : "Reload the preview"} aria-label={zh ? "重新加载预览" : "Reload the preview"}>
            <RefreshCw size={15} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            className="pl-icon-button"
            onClick={() => void window.loom.openLocalArtifact(selected, workspace)}
            title={zh ? "在独立窗口打开" : "Open in its own window"}
            aria-label={zh ? "在独立窗口打开" : "Open in its own window"}
          >
            <ExternalLink size={15} strokeWidth={1.8} />
          </button>
        </> : null}
        closeLabel={zh ? "关闭预览" : "Close the preview"}
        onClose={onClose}
      />

      {!selected ? <div className="artifact-library">
        <div className="artifact-library-tools">
          <div className="artifact-search pl-field"><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={zh ? "搜索文件名或路径…" : "Search files or paths…"} aria-label={zh ? "查找产物" : "Find an artifact"} />{query && <button type="button" onClick={() => setQuery("")} aria-label={zh ? "清空搜索" : "Clear search"}><X size={14} /></button>}</div>
          <div className="artifact-library-toolbar">
            <select aria-label={zh ? "文件类型" : "File type"} value={kind} onChange={event => setKind(event.target.value)}>
              <option value="all">{zh ? "全部类型" : "All types"}</option>
              {availableKinds.map(value => <option value={value} key={value}>{kindLabels[value]}</option>)}
            </select>
            <span role="status">{zh ? `${visibleArtifacts.length} 个文件` : `${visibleArtifacts.length} files`}{(query || kind !== "all") && (zh ? ` / 共 ${artifacts.length} 个` : ` / ${artifacts.length} total`)}</span>
          </div>
          <p className="artifact-library-note">{zh ? "按最近更新排列 · 同一文件的修改自动合并" : "Recently updated · Changes to the same file are merged"}</p>
        </div>
        <div className="artifact-library-list">
          {visibleArtifacts.map(item => {
            const Icon = artifactIcons[artifactRenderer(item.path).kind];
            return <article className="artifact-library-card pl-card is-interactive" key={item.path}>
            <button className="artifact-card-main" type="button" onClick={() => setSelected(item.path)}>
              <span className="artifact-card-icon" data-kind={artifactRenderer(item.path).kind}><Icon size={19} /></span>
              <span className="artifact-card-copy"><strong title={artifactName(item.path)}>{artifactName(item.path)}</strong><small title={item.path}>{directoryLabel(item.path)}</small><span className="artifact-card-meta" data-running={item.running}>{zh ? artifactRenderer(item.path).label : kindLabels[artifactRenderer(item.path).kind]} · {item.running ? (zh ? "更新中" : "updating") : (zh ? "可预览" : "ready")}{item.revision > 1 ? (zh ? ` · ${item.revision} 次更新` : ` · ${item.revision} updates`) : ""}</span></span>
              <ChevronRight className="artifact-card-chevron" size={15} />
            </button>
            <button className="artifact-card-external" type="button" aria-label={zh ? `打开 ${artifactName(item.path)}` : `Open ${artifactName(item.path)}`} title={zh ? "在独立窗口打开" : "Open in its own window"} onClick={() => void window.loom.openLocalArtifact(item.path, workspace)}><ExternalLink size={14} /></button>
          </article>; })}
          {!visibleArtifacts.length ? <div className="artifact-library-empty"><Package size={30} /><strong>{query || kind !== "all" ? (zh ? "没有匹配的产物" : "No matching artifacts") : (zh ? "产物会出现在这里" : "Artifacts appear here")}</strong><span>{query || kind !== "all" ? (zh ? "试试其他文件名或文件类型" : "Try another file name or type") : (zh ? "生成的网页、图片、文档和文件会自动收集。" : "Pages, images, documents and files the work produces are collected automatically.")}</span>{(query || kind !== "all") && <button type="button" onClick={() => { setQuery(""); setKind("all"); }}>{zh ? "重置筛选" : "Reset filters"}</button>}</div> : null}
        </div>
      </div> : <div className="artifact-preview-canvas" data-renderer-kind={renderer.kind}>
        {renderer.side ? (
          <ArtifactRenderSurface
            path={selected}
            workspace={workspace}
            revision={revision + (selectedArtifact?.revision ?? 0)}
          />
        ) : (
          <div className="artifact-preview-state">
            <strong>{zh ? "这个文件暂时没有内置渲染器" : "No built-in preview for this file yet"}</strong>
            <span>{zh ? "可以在独立窗口或系统默认程序中打开。" : "Open it in its own window or the system's default app."}</span>
          </div>
        )}
      </div>}
      </>}
    </aside>,
    document.body,
  );
}

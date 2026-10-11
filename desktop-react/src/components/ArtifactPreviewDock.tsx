import { ArrowLeft, ExternalLink, FileCode2, RefreshCw, X, Search, Package, ChevronRight, Image, FileText, Music, Film, Globe } from "./icons";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { artifactName, artifactRenderer, canRenderArtifact, normalizeArtifactPath } from "../artifactRenderers";
import type { TranscriptItem } from "../types/loom";
import { useMotionPresence } from "../motion/useMotionPresence";
import { ArtifactRenderSurface } from "./ArtifactRenderSurface";
import "./artifact-preview-dock.css";

interface ArtifactPreviewDockProps {
  items: TranscriptItem[];
  open: boolean;
  path: string;
  workspace: string;
  onClose(): void;
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
}: ArtifactPreviewDockProps) {
  const presence = useMotionPresence(open, 420);
  const [width, setWidth] = useState(readPreviewWidth);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState(path);
  const [query, setQuery] = useState("");
  useEffect(() => { setSelected(path); setQuery(""); }, [path, open, workspace]);
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
    document.documentElement.style.setProperty("--artifact-preview-pane-width", `${width}px`);
    return () => {
      document.documentElement.style.removeProperty("--artifact-preview-pane-width");
    };
  }, [width]);

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
      className="artifact-preview-dock"
      data-motion-phase={presence.phase}
      inert={!open}
      data-open={open ? "true" : "false"}
      aria-label="产物侧栏"
    >
      <div
        className="artifact-preview-resizer"
        role="separator"
        aria-label="调整渲染预览宽度"
        aria-orientation="vertical"
        aria-valuemin={Math.round(previewBounds().min)}
        aria-valuemax={Math.round(previewBounds().max)}
        aria-valuenow={width}
        tabIndex={0}
        title="拖动调整预览宽度 · 双击恢复默认"
        onPointerDown={beginResize}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={resizeWithKeyboard}
        onDoubleClick={resetWidth}
      />

      <header className="artifact-preview-header">
        <span className="artifact-preview-mark" aria-hidden={selected ? undefined : true}>
          {selected ? <button type="button" className="artifact-back" aria-label="返回产物列表" onClick={() => setSelected("")}><ArrowLeft size={17} /></button> : <Package size={17} strokeWidth={1.8} />}
        </span>
        <div className="artifact-preview-heading">
          <span>{selected ? "产物 / 预览" : "当前对话"}</span>
          <strong title={selected ? name : undefined}>{selected ? name : `产物 · ${artifacts.length}`}</strong>
          {selected ? <code title={selected}>{selected.replaceAll("\\", "/")}</code> : null}
        </div>
        <div className="artifact-preview-actions">
          {selected ? <><button type="button" onClick={() => setRevision((value) => value + 1)} title="重新加载预览" aria-label="重新加载预览">
            <RefreshCw size={15} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            onClick={() => void window.loom.openLocalArtifact(selected, workspace)}
            title="在独立窗口打开"
            aria-label="在独立窗口打开"
          >
            <ExternalLink size={15} strokeWidth={1.8} />
          </button></> : null}
          <button type="button" onClick={onClose} title="关闭预览" aria-label="关闭预览">
            <X size={16} strokeWidth={1.9} />
          </button>
        </div>
      </header>

      {!selected ? <div className="artifact-library">
        <label className="artifact-search"><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="查找产物…" aria-label="查找产物" /></label>
        <p className="artifact-library-note">网页、文件与交互内容集中在这里，同一文件的更新会合并。</p>
        <div className="artifact-library-list">
          {artifacts.filter(item => item.path.toLowerCase().includes(query.toLowerCase())).map(item => {
            const Icon = artifactIcons[artifactRenderer(item.path).kind];
            return <article className="artifact-library-card" key={item.path}>
            <button className="artifact-card-main" type="button" onClick={() => setSelected(item.path)}>
              <span className="artifact-card-icon"><Icon size={21} /></span>
              <span className="artifact-card-copy"><strong>{artifactName(item.path)}</strong><span>{artifactRenderer(item.path).label} · {item.running ? "更新中" : "可预览"}{item.revision > 1 ? ` · ${item.revision} 次更新` : ""}</span><small title={item.path}>{item.path}</small></span>
              <ChevronRight size={16} />
            </button>
            <button className="artifact-card-external" type="button" aria-label={`打开 ${artifactName(item.path)}`} title="在独立窗口打开" onClick={() => void window.loom.openLocalArtifact(item.path, workspace)}><ExternalLink size={14} /></button>
          </article>; })}
          {!artifacts.length || !artifacts.some(item => item.path.toLowerCase().includes(query.toLowerCase())) ? <div className="artifact-library-empty"><Package size={30} /><strong>{query ? "没有匹配的产物" : "产物会出现在这里"}</strong><span>{query ? "试试其他文件名" : "生成的网页、图片、文档和文件会自动收集。"}</span></div> : null}
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
            <strong>这个文件暂时没有内置渲染器</strong>
            <span>可以在独立窗口或系统默认程序中打开。</span>
          </div>
        )}
      </div>}
    </aside>,
    document.body,
  );
}

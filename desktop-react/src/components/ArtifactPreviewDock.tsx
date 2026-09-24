import { ExternalLink, FileCode2, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { useMotionPresence } from "../motion/useMotionPresence";
import "./artifact-preview-dock.css";

interface ArtifactPreviewDockProps {
  open: boolean;
  path: string;
  workspace: string;
  onClose(): void;
}

const ARTIFACT_DOCK_WIDTH_KEY = "loom.layout.artifactPreviewWidth";
const ARTIFACT_DOCK_DEFAULT = 620;

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

function artifactName(value: string): string {
  const normalized = String(value || "").replaceAll("\\", "/");
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Artifact";
}

export function ArtifactPreviewDock({
  open,
  path,
  workspace,
  onClose,
}: ArtifactPreviewDockProps) {
  const presence = useMotionPresence(open, 420);
  const [width, setWidth] = useState(readPreviewWidth);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const resizeRef = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
    currentWidth: number;
  } | null>(null);
  const name = useMemo(() => artifactName(path), [path]);

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

  useEffect(() => {
    let disposed = false;
    setUrl("");
    setError("");
    if (!open || !path || !workspace) return () => { disposed = true; };

    void window.loom.localArtifactPreviewUrl(path, workspace)
      .then((next) => {
        if (!disposed) setUrl(next);
      })
      .catch((cause) => {
        if (!disposed) setError(cause instanceof Error ? cause.message : String(cause));
      });

    return () => { disposed = true; };
  }, [open, path, workspace, revision]);

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
      data-open={open ? "true" : "false"}
      aria-label="网页与文件渲染预览"
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
        <span className="artifact-preview-mark" aria-hidden="true">
          <FileCode2 size={17} strokeWidth={1.8} />
        </span>
        <div className="artifact-preview-heading">
          <span>渲染预览</span>
          <strong title={name}>{name}</strong>
          <code title={path}>{path.replaceAll("\\", "/")}</code>
        </div>
        <div className="artifact-preview-actions">
          <button type="button" onClick={() => setRevision((value) => value + 1)} title="重新加载预览" aria-label="重新加载预览">
            <RefreshCw size={15} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            onClick={() => void window.loom.openLocalArtifact(path, workspace)}
            title="在独立窗口打开"
            aria-label="在独立窗口打开"
          >
            <ExternalLink size={15} strokeWidth={1.8} />
          </button>
          <button type="button" onClick={onClose} title="关闭预览" aria-label="关闭预览">
            <X size={16} strokeWidth={1.9} />
          </button>
        </div>
      </header>

      <div className="artifact-preview-canvas">
        {error ? (
          <div className="artifact-preview-state is-error">
            <strong>无法渲染这个文件</strong>
            <span>{error}</span>
            <button type="button" onClick={() => setRevision((value) => value + 1)}>重试</button>
          </div>
        ) : url ? (
          <iframe
            key={`${url}:${revision}`}
            className="artifact-preview-frame"
            src={url}
            title={`预览 ${name}`}
            sandbox="allow-scripts allow-forms allow-modals allow-same-origin"
            referrerPolicy="no-referrer"
            allow="fullscreen"
          />
        ) : (
          <div className="artifact-preview-state">
            <span className="artifact-preview-spinner" aria-hidden="true" />
            <strong>正在渲染 {name}</strong>
          </div>
        )}
      </div>
    </aside>,
    document.body,
  );
}

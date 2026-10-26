import { Minus, Plus, X } from "./icons";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import type { MotionPresencePhase } from "../motion/useMotionPresence";
import "./avatar-crop.css";

const CROP_SIZE = 256;
const AVATAR_MAX_BYTES = 40 * 1024;
const AVATAR_SOURCE_MAX_BYTES = 8 * 1024 * 1024;
const AVATAR_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

type ImageInfo = {
  url: string;
  width: number;
  height: number;
};

type DragState = {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
};

interface AvatarCropDialogProps {
  file: File | null;
  /** False while the dialog leaves: the owner keeps `file` until the exit is over. */
  open?: boolean;
  phase?: MotionPresencePhase;
  zh: boolean;
  onCancel(): void;
  onApply(dataUrl: string): void;
}

function encodedDataUrlBytes(dataUrl: string): number {
  const payload = dataUrl.split(",", 2)[1] || "";
  return Math.ceil(payload.length * 3 / 4);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

async function encodeCrop(
  image: HTMLImageElement,
  sourceX: number,
  sourceY: number,
  sourceSize: number,
): Promise<string> {
  for (const size of [192, 160, 128]) {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Image processing is unavailable.");
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(image, sourceX, sourceY, sourceSize, sourceSize, 0, 0, size, size);
    for (const quality of [.88, .8, .72, .64, .56, .48]) {
      const dataUrl = canvas.toDataURL("image/webp", quality);
      if (dataUrl.startsWith("data:image/webp;") && encodedDataUrlBytes(dataUrl) <= AVATAR_MAX_BYTES) {
        return dataUrl;
      }
    }
  }
  throw new Error("This crop could not be compressed enough. Try a simpler image.");
}

export function AvatarCropDialog({ file, open = true, phase = "entered", zh, onCancel, onApply }: AvatarCropDialogProps) {
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [image, setImage] = useState<ImageInfo | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const [error, setError] = useState("");
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    if (!file) {
      setImage(null);
      setError("");
      return;
    }
    if (!AVATAR_TYPES.has(file.type)) {
      setImage(null);
      setError(zh ? "请选择 PNG、JPEG 或 WebP 图片。" : "Use a PNG, JPEG, or WebP image.");
      return;
    }
    if (file.size > AVATAR_SOURCE_MAX_BYTES) {
      setImage(null);
      setError(zh ? "请选择小于 8 MB 的图片。" : "Choose an image smaller than 8 MB.");
      return;
    }

    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.onload = () => {
      setImage({ url, width: probe.naturalWidth, height: probe.naturalHeight });
      setZoom(1);
      setOffsetX(0);
      setOffsetY(0);
      setError("");
    };
    probe.onerror = () => {
      setImage(null);
      setError(zh ? "无法读取这张图片。" : "Could not read that image.");
    };
    probe.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file, zh]);

  useEffect(() => {
    if (!file || !open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [file, open, onCancel]);

  const baseScale = useMemo(() => {
    if (!image) return 1;
    return Math.max(CROP_SIZE / image.width, CROP_SIZE / image.height);
  }, [image]);
  const scale = baseScale * zoom;
  const renderedWidth = image ? image.width * scale : CROP_SIZE;
  const renderedHeight = image ? image.height * scale : CROP_SIZE;
  const maxX = Math.max(0, (renderedWidth - CROP_SIZE) / 2);
  const maxY = Math.max(0, (renderedHeight - CROP_SIZE) / 2);

  useEffect(() => {
    setOffsetX((value) => clamp(value, -maxX, maxX));
    setOffsetY((value) => clamp(value, -maxY, maxY));
  }, [maxX, maxY]);

  const moveTo = (x: number, y: number) => {
    setOffsetX(clamp(x, -maxX, maxX));
    setOffsetY(clamp(y, -maxY, maxY));
  };

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!image || applying) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: offsetX,
      originY: offsetY,
    };
  };

  const drag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = dragRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    moveTo(state.originX + event.clientX - state.startX, state.originY + event.clientY - state.startY);
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const changeZoom = (next: number) => setZoom(clamp(next, 1, 3));

  const onWheelZoom = (event: WheelEvent<HTMLDivElement>) => {
    if (!image || applying) return;
    event.preventDefault();
    changeZoom(zoom + (event.deltaY < 0 ? .08 : -.08));
  };

  const applyCrop = async () => {
    const element = imageRef.current;
    if (!image || !element) return;
    setApplying(true);
    setError("");
    try {
      const sourceSize = CROP_SIZE / scale;
      const sourceX = clamp((image.width - sourceSize) / 2 - offsetX / scale, 0, image.width - sourceSize);
      const sourceY = clamp((image.height - sourceSize) / 2 - offsetY / scale, 0, image.height - sourceSize);
      onApply(await encodeCrop(element, sourceX, sourceY, sourceSize));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApplying(false);
    }
  };

  if (!file) return null;

  return (
    <div className="loom-avatar-crop-backdrop pl-scrim pl-reveal" data-motion-phase={phase} inert={!open} role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !applying) onCancel();
    }}>
      <section className="loom-avatar-crop-dialog pl-modal" role="dialog" aria-modal="true" aria-labelledby="loom-avatar-crop-title" aria-describedby="loom-avatar-crop-lead">
        <header className="loom-avatar-crop-header">
          <div>
            <h3 id="loom-avatar-crop-title">{zh ? "裁剪头像" : "Crop photo"}</h3>
            <p id="loom-avatar-crop-lead">{zh ? "拖动调整位置，滚轮或滑块缩放。" : "Drag to place it; scroll or use the slider to zoom."}</p>
          </div>
          <button type="button" autoFocus className="loom-avatar-crop-close pl-icon-button" onClick={onCancel} disabled={applying} aria-label={zh ? "取消裁剪" : "Cancel crop"}>
            <X size={16} />
          </button>
        </header>

        <div className="loom-avatar-crop-body">
          <div
            className={`loom-avatar-crop-stage ${image ? "is-ready" : ""}`}
            style={{ width: CROP_SIZE, height: CROP_SIZE }}
            onPointerDown={beginDrag}
            onPointerMove={drag}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onWheel={onWheelZoom}
            tabIndex={image ? 0 : -1}
            role={image ? "group" : undefined}
            aria-label={image
              ? (zh ? "头像取景。方向键移动图片，按住 Shift 移动更多。" : "Photo framing. Arrow keys move the photo; hold Shift to move it further.")
              : undefined}
            onKeyDown={(event) => {
              if (!image) return;
              const step = event.shiftKey ? 12 : 4;
              if (event.key === "ArrowLeft") { event.preventDefault(); moveTo(offsetX - step, offsetY); }
              if (event.key === "ArrowRight") { event.preventDefault(); moveTo(offsetX + step, offsetY); }
              if (event.key === "ArrowUp") { event.preventDefault(); moveTo(offsetX, offsetY - step); }
              if (event.key === "ArrowDown") { event.preventDefault(); moveTo(offsetX, offsetY + step); }
            }}
          >
            {image ? (
              <img
                ref={imageRef}
                src={image.url}
                alt=""
                draggable={false}
                style={{
                  width: renderedWidth,
                  height: renderedHeight,
                  transform: `translate(-50%, -50%) translate(${offsetX}px, ${offsetY}px)`,
                }}
              />
            ) : (
              <div className="loom-avatar-crop-loading">{error || (zh ? "正在读取图片…" : "Loading image…")}</div>
            )}
            {/* The avatar is round: what falls outside the circle is dimmed, and only the circle is kept in view. */}
            {image ? <span className="loom-avatar-crop-window" aria-hidden="true" /> : null}
          </div>

          <div className="loom-avatar-crop-controls">
            <button type="button" className="loom-avatar-crop-step pl-icon-button" onClick={() => changeZoom(zoom - .25)} disabled={!image || applying || zoom <= 1} aria-label={zh ? "缩小" : "Zoom out"}>
              <Minus size={14} />
            </button>
            <input
              type="range"
              min="1"
              max="3"
              step="0.01"
              style={{ "--crop-fill": `${((zoom - 1) / 2) * 100}%` } as CSSProperties}
              value={zoom}
              onChange={(event) => changeZoom(Number(event.target.value))}
              disabled={!image || applying}
              aria-label={zh ? "头像缩放" : "Avatar zoom"}
              aria-valuetext={`${Math.round(zoom * 100)}%`}
            />
            <button type="button" className="loom-avatar-crop-step pl-icon-button" onClick={() => changeZoom(zoom + .25)} disabled={!image || applying || zoom >= 3} aria-label={zh ? "放大" : "Zoom in"}>
              <Plus size={14} />
            </button>
            <span className="loom-avatar-crop-value">{Math.round(zoom * 100)}%</span>
          </div>

          {error && image ? <p className="loom-avatar-crop-error" role="alert">{error}</p> : null}
        </div>

        <footer className="loom-avatar-crop-actions">
          <button
            type="button"
            className="loom-avatar-crop-reset"
            data-press="none"
            onClick={() => { setZoom(1); setOffsetX(0); setOffsetY(0); }}
            disabled={!image || applying || (zoom === 1 && offsetX === 0 && offsetY === 0)}
          >
            {zh ? "重置" : "Reset"}
          </button>
          <button type="button" onClick={onCancel} disabled={applying}>{zh ? "取消" : "Cancel"}</button>
          <button type="button" className="is-primary" onClick={() => void applyCrop()} disabled={!image || applying}>
            {applying ? (zh ? "处理中…" : "Processing…") : (zh ? "使用头像" : "Use photo")}
          </button>
        </footer>
      </section>
    </div>
  );
}

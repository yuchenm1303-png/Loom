import { ExternalLink, X } from "./icons";
import { useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useMotionPresence } from "../motion/useMotionPresence";
import "./image-lightbox.css";

/** Shared lifetime and keyboard behavior for user and assistant image previews. */
export function ImageLightbox({ open, source, label, path, onClose, onReveal }: {
  open: boolean;
  source: string;
  label: string;
  path: string;
  onClose(): void;
  onReveal(): void;
}) {
  const presence = useMotionPresence(open, 200);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  // Presence mounts in a layout effect; focus once its DOM exists as well.
  useLayoutEffect(() => {
    if (open && presence.mounted) closeRef.current?.focus();
  }, [open, presence.mounted]);
  if (!presence.mounted) return null;
  return createPortal(
    <div className="user-message-image-lightbox" data-motion-phase={presence.phase} inert={!open}
      role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}>
      <div ref={panelRef} className="user-message-image-lightbox-panel" role="dialog" aria-modal="true"
        aria-label={`查看图片 ${label}`} onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            onClose();
          }
          if (event.key === "Tab") {
            const buttons = panelRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
            if (!buttons?.length) return;
            const first = buttons[0];
            const last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault(); first.focus();
            }
          }
        }}>
        <div className="user-message-image-lightbox-toolbar">
          <strong title={path}>{label}</strong>
          <div className="user-message-image-lightbox-actions">
            <button type="button" onClick={onReveal} title="在文件夹中查看">
              <ExternalLink size={15} strokeWidth={1.8} /><span>原文件</span>
            </button>
            <button ref={closeRef} type="button" className="icon-only" onClick={onClose}
              title="关闭图片预览" aria-label="关闭图片预览"><X size={17} strokeWidth={1.9} /></button>
          </div>
        </div>
        <div className="user-message-image-lightbox-canvas">
          <img src={source} alt={label} draggable={false} data-loom-image-path={path} />
        </div>
      </div>
    </div>, document.body,
  );
}

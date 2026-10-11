import { useEffect, useRef, useState, type ReactNode } from "react";
import { Minus, Square, Copy, X } from "./icons";
import "./window-chrome.css";

export function WindowChrome({ children }: { children: ReactNode }) {
  const control = window.loom?.windowControl;
  const [maximized, setMaximized] = useState(false);
  const drag = useRef<{ x: number; y: number; windowX: number; windowY: number } | null>(null);
  const frame = useRef<number | null>(null);
  const pending = useRef<{ x: number; y: number } | null>(null);
  const dragVersion = useRef(0);
  useEffect(() => {
    if (!control) return;
    let disposed = false;
    const sync = () => void control("state").then((state) => { if (state && !disposed) setMaximized(state.maximized); });
    sync();
    window.addEventListener("resize", sync);
    return () => { disposed = true; window.removeEventListener("resize", sync); if (frame.current !== null) cancelAnimationFrame(frame.current); };
  }, [control]);
  if (!control) return <>{children}</>;
  const stopDrag = () => {
    dragVersion.current++;
    drag.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    if (pending.current) void control("move", pending.current);
    pending.current = null;
  };
  const maximize = () => void control("maximize").then((state) => { if (state) setMaximized(state.maximized); });
  return <div className="loom-window-shell">
    <header className="loom-titlebar" data-ic-row>
      <div className="loom-titlebar-drag" onDoubleClick={maximize}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          const version = ++dragVersion.current;
          const x = event.screenX, y = event.screenY;
          void control("state").then((state) => {
            if (state && !state.maximized && dragVersion.current === version) drag.current = { x, y, windowX: state.x, windowY: state.y };
          });
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) return;
          pending.current = { x: start.windowX + event.screenX - start.x, y: start.windowY + event.screenY - start.y };
          if (frame.current === null) frame.current = requestAnimationFrame(() => {
            frame.current = null;
            if (pending.current) void control("move", pending.current);
            pending.current = null;
          });
        }} onPointerUp={stopDrag} onPointerCancel={stopDrag} onLostPointerCapture={stopDrag}>
        <span>Loom</span>
      </div>
      <button aria-label="Minimize window" title="最小化" onClick={() => void control("minimize")}><Minus size={16} /></button>
      <button aria-label={maximized ? "Restore window" : "Maximize window"} title={maximized ? "还原" : "最大化"} onClick={maximize}>{maximized ? <Copy size={13} /> : <Square size={13} />}</button>
      <button className="loom-window-close" aria-label="Close window" title="关闭" onClick={() => void control("close")}><X size={18} /></button>
    </header>
    <div className="loom-window-content">{children}</div>
  </div>;
}

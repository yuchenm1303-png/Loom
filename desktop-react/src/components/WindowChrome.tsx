import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { Minus, Square, Copy, X } from "./icons";
import { useI18n } from "../i18n";
import "./window-chrome.css";

export function WindowChrome({ children }: { children: ReactNode }) {
  const control = window.loom?.windowControl;
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [maximized, setMaximized] = useState(false);
  const [focused, setFocused] = useState(() => document.hasFocus());
  useLayoutEffect(() => {
    if (!control) return;
    document.documentElement.classList.add("loom-desktop-chrome");
    return () => document.documentElement.classList.remove("loom-desktop-chrome");
  }, [control]);
  useEffect(() => {
    if (!control) return;
    let disposed = false;
    const sync = () => void control("state").then((state) => {
      if (state && !disposed) setMaximized(state.maximized);
    });
    const focus = () => setFocused(true);
    const blur = () => setFocused(false);
    sync();
    window.addEventListener("resize", sync);
    window.addEventListener("focus", focus);
    window.addEventListener("blur", blur);
    return () => {
      disposed = true;
      window.removeEventListener("resize", sync);
      window.removeEventListener("focus", focus);
      window.removeEventListener("blur", blur);
    };
  }, [control]);
  if (!control) return <>{children}</>;
  const maximize = () => void control("maximize").then((state) => {
    if (state) setMaximized(state.maximized);
  });
  const minimizeLabel = zh ? "最小化" : "Minimize";
  const maximizeLabel = maximized ? (zh ? "还原" : "Restore") : (zh ? "最大化" : "Maximize");
  const closeLabel = zh ? "关闭" : "Close";
  return <div className="loom-window-shell" data-window-focused={focused}>
    <header className="loom-titlebar">
      <div className="loom-titlebar-drag" aria-hidden="true" />
      <div className="loom-window-controls">
        <button type="button" aria-label={minimizeLabel} title={minimizeLabel} onClick={() => void control("minimize")}><Minus size={14} /></button>
        <button type="button" aria-label={maximizeLabel} title={maximizeLabel} onClick={maximize}>{maximized ? <Copy size={12} /> : <Square size={12} />}</button>
        <button type="button" className="loom-window-close" aria-label={closeLabel} title={closeLabel} onClick={() => void control("close")}><X size={15} /></button>
      </div>
    </header>
    <div className="loom-window-content">{children}</div>
  </div>;
}

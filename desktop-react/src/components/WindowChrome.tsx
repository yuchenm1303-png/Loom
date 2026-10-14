import { useEffect, useLayoutEffect, useState, type ReactNode, type SVGProps } from "react";
import { useI18n } from "../i18n";
import "./window-chrome.css";

// Caption glyphs are drawn here, not taken from the icon set: they sit on the same rail as the
// header's own 16-18px icons, so they are a size down (a 12px box) and a hair lighter.
const glyph: SVGProps<SVGSVGElement> = {
  width: 12, height: 12, viewBox: "0 0 12 12", fill: "none", stroke: "currentColor",
  strokeWidth: 1.25, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: false,
};
const MinimizeGlyph = () => <svg {...glyph}><path d="M1.5 6h9" /></svg>;
const MaximizeGlyph = () => <svg {...glyph}><rect x="1.5" y="1.5" width="9" height="9" rx="2.25" /></svg>;
const RestoreGlyph = () => <svg {...glyph}><path d="M4 2.9v-.15A1.25 1.25 0 0 1 5.25 1.5h5.25a1.25 1.25 0 0 1 1.25 1.25V8a1.25 1.25 0 0 1-1.25 1.25h-.15" /><rect x="1.5" y="3.5" width="7" height="7" rx="1.75" /></svg>;
const CloseGlyph = () => <svg {...glyph}><path d="m2 2 8 8M10 2 2 10" /></svg>;

/**
 * The desktop window has no native frame, so the window controls are drawn here. They float over
 * the top right corner of the app instead of owning a strip of their own: the sidebar, the thread
 * header and every panel start at the very top of the window, and the top --loom-rail-height of
 * it is "the rail" those surfaces share (window-rail.css says how they leave room for the
 * controls and keep the rail draggable). On the web there is no such window and this renders only
 * its children.
 */
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
  // The drag layer comes before the content in the tree on purpose: Chromium builds the draggable
  // region in tree order, so the no-drag controls that follow it cut their own holes into it.
  return <div className="loom-window-shell" data-window-focused={focused}>
    <header className="loom-titlebar">
      <div className="loom-titlebar-drag" aria-hidden="true" />
      <div className="loom-window-controls">
        <button type="button" className="loom-window-minimize" aria-label={minimizeLabel} title={minimizeLabel} onClick={() => void control("minimize")}><MinimizeGlyph /></button>
        <button type="button" className="loom-window-maximize" aria-label={maximizeLabel} title={maximizeLabel} onClick={maximize}>{maximized ? <RestoreGlyph /> : <MaximizeGlyph />}</button>
        <button type="button" className="loom-window-close" aria-label={closeLabel} title={closeLabel} onClick={() => void control("close")}><CloseGlyph /></button>
      </div>
    </header>
    <div className="loom-window-content">{children}</div>
  </div>;
}

/**
 * The live "working" glyph (ThinkingGlyph) at real size inside a thinking row, enlarged to judge its drawing, and
 * beside Loom's original mark at the same sizes.
 *
 *   open /scripts/fixtures/thinking-glyph.html
 *
 *   ?theme=dark|light   (default dark)
 *   ?still=0.25         hold every animation at this fraction of the cycle (a still to look at)
 *   ?row=1              only the thinking row (for measuring what the live glyph costs)
 *   ?row=0              nothing at all (the baseline for that measurement)
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../../src/renderer-styles";
import { ThinkingGlyph } from "../../src/components/ThinkingGlyph";
import original from "./mark-lab/loom-mark-512.png";

const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
const still = params.get("still");
const row = params.get("row");
document.documentElement.dataset.loomTheme = theme;
document.documentElement.style.colorScheme = theme;
document.body.style.cssText = "margin:0;padding:24px;font:12px/1.4 system-ui,\"Segoe UI\",sans-serif;" +
  (theme === "dark" ? "background:#121318;color:#8f95a1;" : "background:#f6f6f8;color:#6a707c;");
if (still !== null) {
  const style = document.createElement("style");
  // A negative delay of the cycle's fraction holds every animation at that point.
  style.textContent = `.tg, .tg * { animation-play-state: paused !important; animation-delay: calc(var(--loom-period-shimmer) * -${Number(still)}) !important; }`;
  document.head.append(style);
}

const SIZES = [22, 64, 256];

function Scaled({ size }: { size: number }) {
  const scale = size / 22;
  return (
    <div style={{ width: size, height: size, position: "relative" }}>
      <div style={{ position: "absolute", left: 0, top: 0, transform: `scale(${scale})`, transformOrigin: "0 0" }}>
        <ThinkingGlyph />
      </div>
    </div>
  );
}

function App() {
  if (row !== null) {
    return row === "1" ? <div className="inline-thinking" role="status"><ThinkingGlyph /><span className="thinking-shimmer">正在思考…</span></div> : null;
  }
  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div className="inline-thinking" role="status">
        <ThinkingGlyph />
        <span className="thinking-shimmer">正在思考…</span>
      </div>
      {[["original", (size: number) => <img src={original} width={size} height={size} alt="" />],
        ["vector", (size: number) => <Scaled size={size} />]].map(([name, draw]) => (
        <div key={name as string} data-row={name as string} style={{ display: "flex", gap: 28, alignItems: "flex-end" }}>
          <span style={{ width: 52 }}>{name as string}</span>
          {SIZES.map((size) => <div key={size}>{(draw as (size: number) => JSX.Element)(size)}</div>)}
        </div>
      ))}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

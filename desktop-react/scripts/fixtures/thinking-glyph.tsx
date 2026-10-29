/**
 * The live "working" glyph (ThinkingGlyph) at real size inside a thinking row, and enlarged to judge its drawing.
 *
 *   open /scripts/fixtures/thinking-glyph.html
 *
 *   ?theme=dark|light   (default dark)
 *   ?still=0.25         pause every animation at this fraction of the cycle (a still to look at)
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../../src/renderer-styles";
import { ThinkingGlyph } from "../../src/components/ThinkingGlyph";

const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
const still = params.get("still");
document.documentElement.dataset.loomTheme = theme;
document.documentElement.style.colorScheme = theme;
document.body.style.cssText = "margin:0;padding:28px;font:14px/1.4 system-ui,\"Segoe UI\",sans-serif;" +
  (theme === "dark" ? "background:#121318;color:#c9ccd6;" : "background:#f6f6f8;color:#3a3d48;");
if (still !== null) {
  const style = document.createElement("style");
  // A negative delay of the cycle's fraction holds every animation at that point.
  style.textContent = `.tg, .tg * { animation-play-state: paused !important; animation-delay: calc(var(--loom-period-shimmer) * -${Number(still)}) !important; }`;
  document.head.append(style);
}

function App() {
  return (
    <div style={{ display: "grid", gap: 28 }}>
      <div className="inline-thinking" role="status">
        <ThinkingGlyph />
        <span className="thinking-shimmer">正在思考…</span>
      </div>
      <div style={{ display: "flex", gap: 40, alignItems: "center" }}>
        {[4, 8].map((scale) => (
          <div key={scale} data-scale={scale} style={{ width: 22 * scale, height: 22 * scale, position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, transform: `scale(${scale})`, transformOrigin: "0 0" }}>
              <ThinkingGlyph />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

/**
 * Icon lab: every redrawn glyph, in the real stylesheet cascade, at rest and in
 * each state the motion layer knows (engaged, pressed, expanded). Static pictures
 * of the states come from the `data-ic-force` hooks; real hover works as usual.
 *
 *   open /scripts/fixtures/icon-lab.html
 *
 *   ?theme=dark|light     (default dark)
 *   ?only=trash,copy      show only these glyphs (data-icon names)
 *   ?size=16              glyph size of the state columns (default 16)
 *
 * window.iconLab.names lists every glyph; window.iconLab.cell(name, column)
 * returns the button element for a column ("rest" | "on" | "down" | "open" | "live").
 */
import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../../src/renderer-styles";
import * as Glyphs from "../../src/components/icons/glyphs";
import type { IconComponent } from "../../src/components/icons";

const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
const size = Number(params.get("size") || 16);
const only = (params.get("only") || "").split(",").filter(Boolean);
document.documentElement.dataset.loomTheme = theme;
document.documentElement.style.colorScheme = theme;
document.body.style.cssText = `margin:0;padding:18px 24px 40px;font:12px/1.4 system-ui,"Segoe UI",sans-serif;` +
  (theme === "dark" ? "background:#101116;color:#c9ccd6;" : "background:#f4f4f7;color:#3a3d48;");

// Glyphs drawn on their own box say so, the way the app passes it.
const ownBox: Record<string, Record<string, string>> = {
  "sidebar-triangle": { viewBox: "0 0 12 14", fill: "currentColor", stroke: "none" },
};

const columns = [
  { key: "rest", label: "rest", attrs: {} },
  { key: "on", label: "engaged", attrs: { "data-ic-force": "on" } },
  { key: "down", label: "pressed", attrs: { "data-ic-force": "on down" } },
  { key: "open", label: "expanded", attrs: { "aria-expanded": true } },
  { key: "open-on", label: "expanded + engaged", attrs: { "aria-expanded": true, "data-ic-force": "on" } },
  { key: "live", label: "live (hover me)", attrs: {} },
] as const;

const entries = Object.entries(Glyphs as Record<string, IconComponent>)
  .map(([exportName, Icon]) => {
    const name = String(Icon.iconName || exportName);
    return { exportName, Icon, name };
  })
  // Loader2 is an alias of LoaderCircle
  .filter((entry, index, all) => all.findIndex((other) => other.Icon === entry.Icon) === index);

const ink = theme === "dark" ? "#d8dbe6" : "#2d3040";
const wash = theme === "dark" ? "rgba(255,255,255,.07)" : "rgba(0,0,0,.06)";
const line = theme === "dark" ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.09)";

function Cell({ Icon, column, glyphSize, strokeWidth, name }: { Icon: IconComponent; column: (typeof columns)[number]; glyphSize: number; strokeWidth: number; name: string }) {
  return (
    <button
      type="button"
      data-lab-cell={column.key}
      {...column.attrs}
      style={{ width: glyphSize + 18, height: glyphSize + 18, display: "grid", placeItems: "center", border: `1px solid ${line}`, borderRadius: 9,
        background: "transparent", color: ink, cursor: "pointer", padding: 0 }}
    >
      <Icon size={glyphSize} strokeWidth={strokeWidth} {...(ownBox[name] ?? {})} />
    </button>
  );
}

function Lab() {
  const shown = entries.filter((entry) => !only.length || only.includes(entry.name) || only.includes(entry.exportName));
  return (
    <div>
      <style>{`.lab-row button:hover{background:${wash}!important}`}</style>
      <div style={{ display: "grid", gridTemplateColumns: `150px repeat(${columns.length}, ${size + 48}px)`, gap: "6px 10px", alignItems: "center" }}>
        <div />
        {columns.map((column) => <div key={column.key} style={{ opacity: .6, fontSize: 10.5 }}>{column.label}</div>)}
        {shown.map(({ exportName, Icon, name }) => (
          <React.Fragment key={exportName}>
            <div className="lab-row" style={{ opacity: .85 }}>{name}</div>
            {columns.map((column) => (
              <div key={column.key} className="lab-row" data-lab-row={name}>
                <Cell Icon={Icon} column={column} glyphSize={size} strokeWidth={1.75} name={name} />
              </div>
            ))}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

Reflect.set(window, "iconLab", {
  names: entries.map((entry) => entry.name),
  cell(name: string, column: string) {
    return document.querySelector(`[data-lab-row="${name}"] [data-lab-cell="${column}"]`);
  },
});
createRoot(document.getElementById("root")!).render(<StrictMode><Lab /></StrictMode>);

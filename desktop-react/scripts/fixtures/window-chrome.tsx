import React, { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WindowChrome } from "../../src/components/WindowChrome";
import "../../src/styles.css";
import "../../src/customScrollbars";
import "../../src/pointerClick";
const actions: { action: string; point?: { x: number; y: number } }[] = [];
let maximized = false;
Reflect.set(window, "windowActions", actions);
Reflect.set(window, "loom", { windowControl: async (action: string, point?: { x: number; y: number }) => {
  actions.push({ action, point });
  if (action === "maximize") maximized = !maximized;
  return { maximized, x: 20, y: 30, width: 900, height: 700 };
} });
createRoot(document.getElementById("root")!).render(<StrictMode><WindowChrome>
  <div className="test-scroll" style={{ height: 200, width: 300, overflow: "auto", margin: 40 }}>
    <div style={{ height: 1600, width: 1200 }}>Scrollable content</div>
  </div>
</WindowChrome></StrictMode>);

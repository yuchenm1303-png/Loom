import { createRoot } from "react-dom/client";
import { BuildHistory, type BuildHistoryEntry } from "../../src/components/BuildHistory";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
import "../../src/components/project-flow.css";
const params = new URLSearchParams(location.search);
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
const entries: BuildHistoryEntry[] = Array.from({ length: 85 }, (_, i) => ({
  id: `t${i}`, title: `Build task ${i}`, when: `${i + 1}m`, current: i === 70, live: i === 2,
  projectId: i % 2 ? "other" : "loom", workspace: i % 2 ? "C:/work/other" : "C:/work/Loom", projectName: i % 2 ? "Other app" : "Loom",
  objective: i === 84 ? "Unique oldest objective" : "Ship a working app", status: i === 4 ? "needs_you" : i === 70 ? "paused" : "running", done: 2, total: 8,
}));
createRoot(document.getElementById("root")!).render(<div className="pf-stage" style={{ display: "block", overflow: "auto", padding: 24 }}>
  <section className="pf-card pf-listed pf-history" style={{ width: "min(340px, calc(100vw - 48px))" }}>
    <BuildHistory entries={entries} lang={params.get("lang") === "zh" ? "zh" : "en"}
      onOpen={async id => { (window as any).opened = id; if ((window as any).failOpen) throw new Error("offline"); }}
      onNew={() => { (window as any).created = true; }} />
  </section></div>);

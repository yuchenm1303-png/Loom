import { useState } from "react";
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
const w = window as any;
// Rename, archive and delete change the list the way the app's thread list does; window.failAction makes them throw.
function Card() {
  const [list, setList] = useState(entries);
  const act = async (job: () => void) => { await new Promise(done => setTimeout(done, 30)); if (w.failAction) throw new Error("offline"); job(); };
  return <BuildHistory entries={list} lang={params.get("lang") === "zh" ? "zh" : "en"}
    onOpen={async id => { w.opened = id; if (w.failOpen) throw new Error("offline"); }}
    onNew={() => { w.created = true; }}
    onRename={(id, title) => act(() => { w.renamed = [id, title]; setList(rows => rows.map(row => row.id === id ? { ...row, title } : row)); })}
    onArchive={id => act(() => { w.archived = id; setList(rows => rows.filter(row => row.id !== id)); })}
    onDelete={id => act(() => { w.deleted = id; setList(rows => rows.filter(row => row.id !== id)); })} />;
}
createRoot(document.getElementById("root")!).render(<div className="pf-stage" style={{ display: "block", overflow: "auto", padding: 24 }}>
  <section className="pf-card pf-listed pf-history" style={{ width: "min(340px, calc(100vw - 48px))" }}>
    <Card />
  </section></div>);

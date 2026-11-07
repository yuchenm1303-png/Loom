import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectFlow, type FlowData } from "../../src/components/ProjectFlow";
import { ProjectWorkspacePicker } from "../../src/components/ProjectWorkspacePicker";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
const projects = Array.from({ length: 14 }, (_, index) => ({
  id: String(index), name: index ? `Project ${index} with a longer name` : "Loom",
  root: `C:/Users/example/projects/workspace-${index}`, threadCount: 0,
}));
const data: FlowData = {
  phase: "draft", objective: "", narration: { summary: "", text: "Describe what you want to build." },
  milestone: 1, state: "starting", model: "", repository: { name: "Loom", path: projects[0].root, commit: "", subject: "", branch: "main" },
  scope: [], knowledge: { checks: 0, notes: 0, latest: "" }, tasks: [], agents: [],
  budget: { tokens: 0, tokenLimit: 8000000 }, workers: 2, maxWorkers: 4,
  controls: { pause: false, resume: false, cancel: false },
};
function Preview() {
  const [selected, setSelected] = useState("0");
  const [locked, setLocked] = useState(false);
  return <div style={{ height: "100vh" }}>
    <button style={{ position: "fixed", zIndex: 10 }} onClick={() => setLocked(value => !value)}>Toggle lock</button>
    <output data-selected={selected} />
    <div className="project-mode-stage" style={{ height: "100%" }}>
      <ProjectFlow lang="en" data={{ ...data, repository: { ...data.repository, name: projects[Number(selected)].name, path: projects[Number(selected)].root } }}
        actions={{ onChat: () => {}, workspaceControls: <ProjectWorkspacePicker projects={projects}
          workspace={{ path: projects[Number(selected)].root, status: "ready" }} locked={locked} lang="en" branch="main"
          onSelect={setSelected} onBrowse={() => setSelected("13")} /> }} />
    </div>
  </div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Preview /></I18nProvider>);

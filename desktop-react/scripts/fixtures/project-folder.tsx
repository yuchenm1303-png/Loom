import { useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { Sidebar } from "../../src/components/Sidebar";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
applyThemePreference("dark", { animate: false, syncNative: false });
let picked = "C:\\Projects\\我的新项目";
const calls: string[] = [];
(window as any).folderFixture = { calls, cancel: () => { picked = ""; } };
(window as any).loom = {
  pickDirectory: async () => { calls.push("pick"); return picked; },
  call: async () => ({ run: params.has("live") ? { id: "run", objective: "Build the project", episodesUsed: 0, maxEpisodes: 400, state: "running", phase: "planning", control: "active", plan: [],
      repository: "C:\\Projects\\original", baseCommit: "abc1234", completionRef: "refs/heads/main", tokensUsed: 0, tokenBudget: 8000000, maxWorkers: 2 } : null,
    program: null, children: [], agents: [], events: [], workspace: { git: false, changeCount: 0, changes: [] } }),
};
function Fixture() {
  const [workspace, setWorkspace] = useState("C:\\Projects\\original");
  const noop = async () => {};
  if (params.has("sidebar")) return <Sidebar threads={[]} projects={[]} projectsSupported={true} threadView="active" archivedCount={0}
    searchOpen={false} onSearchOpenChange={() => {}} onOpen={noop} onNew={noop} onOpenProject={noop}
    onAddProject={async root => { calls.push(root); }} onRenameProject={noop} onRemoveProject={noop} onMoveProject={noop}
    onRename={noop} onArchive={noop} onDelete={noop} onFork={noop} onViewChange={noop} />;
  return <div style={{height:"100vh", display:"grid"}}><ProjectMode threadId="folder-thread" workspace={workspace}
    onChangeWorkspace={async path => { calls.push(path); if (params.has("fail")) throw new Error("Folder unavailable"); setWorkspace(path); }} /></div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);

import { useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { Sidebar } from "../../src/components/Sidebar";
import type { ProjectRecord } from "../../src/types/loom";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
applyThemePreference("dark", { animate: false, syncNative: false });
let picked = "C:\\Projects\\我的新项目";
const calls: string[] = [];
let mockPath = "C:\\Projects\\original";
let setupStatus = params.get("setup") || "";
(window as any).folderFixture = { calls, cancel: () => { picked = ""; } };
(window as any).loom = {
  pickDirectory: async () => { calls.push("pick"); return picked; },
  call: async (method: string, request: any) => {
    if (method === "projectRun/prepareWorkspace") {
      calls.push(`prepare:${request.action}:${request.confirmed}`);
      setupStatus = "ready";
      return {};
    }
    return { run: params.has("live") ? { id: "run", objective: "Build the project", episodesUsed: 0, maxEpisodes: 400, state: "running", phase: "planning", control: "active", plan: [],
      repository: "C:\\Projects\\original", baseCommit: "abc1234", completionRef: "refs/heads/main", tokensUsed: 0, tokenBudget: 8000000, maxWorkers: 2 } : null,
    program: null, children: [], agents: [], events: [],
    proposal: params.has("setup") ? { id: "proposal", objective: "Build the project", tokenBudget: 8000000, maxWorkers: 2 } : null,
    workspace: { path: mockPath, status: setupStatus || undefined, git: setupStatus === "ready", branch: setupStatus === "ready" ? "main" : "", changeCount: 0, changes: [] } };
  },
};
function Fixture() {
  const [workspace, setWorkspace] = useState("C:\\Projects\\original");
  const noop = async () => {};
  if (params.has("sidebar")) return <Sidebar threads={[]} projects={[]} projectsSupported={true} threadView="active" archivedCount={0}
    searchOpen={false} onSearchOpenChange={() => {}} onOpen={noop} onNew={noop} onOpenProject={noop}
    onAddProject={async root => { calls.push(root); }} onRenameProject={noop} onRemoveProject={noop} onMoveProject={noop}
    onRename={noop} onArchive={noop} onDelete={noop} onFork={noop} onViewChange={noop} />;
  return <div style={{height:"100vh", display:"grid"}}><ProjectMode threadId="folder-thread" workspace={workspace}
    projects={[{ id: "existing", name: "已有项目", root: "C:\\Projects\\已有项目" } as ProjectRecord]}
    onSelectProject={async id => { calls.push(`project:${id}`); mockPath = "C:\\Projects\\已有项目"; setWorkspace(mockPath); }}
    onChangeWorkspace={async path => { calls.push(path); if (params.has("fail")) throw new Error("Folder unavailable"); mockPath = path; setWorkspace(path); }} /></div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);

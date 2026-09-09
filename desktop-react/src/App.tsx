import { PanelRightOpen, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { RunProgress } from "./components/RunProgress";
import { Sidebar } from "./components/Sidebar";
import { Transcript } from "./components/Transcript";
import "./components/inline-thinking.css";
import { useLoom } from "./state/useLoom";

export default function App() {
  const loom = useLoom();
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const thread = loom.active?.thread;
  const running = loom.turnActive || thread?.status === "running" || thread?.status === "waiting_approval";
  const archived = Boolean(thread?.archived);
  const conversationDisabled = !thread || loom.connection !== "ready" || running || archived;

  const progressProps = {
    items: loom.items,
    startedAt: loom.turnStartedAt,
    threadStatus: thread?.status,
    currentTurnId: thread?.currentTurnId,
    totalTokens: thread?.usage?.totalTokens,
  };

  if (loom.connection === "error") {
    return (
      <div className="boot-error">
        <div className="boot-error-card">
          <div className="brand-mark large">L</div>
          <h1>Loom App Server did not start</h1>
          <p>{loom.error || "Unknown connection error"}</p>
          <button className="button primary" onClick={() => window.location.reload()}><RotateCcw size={15} /> Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className={`app-shell ${inspectorOpen ? "with-inspector" : ""}`}>
      <Sidebar
        threads={loom.threads}
        activeId={thread?.id}
        threadView={loom.threadView}
        archivedCount={loom.threadCounts.archived}
        onOpen={loom.openThread}
        onNew={loom.newThread}
        onRename={loom.renameThread}
        onArchive={loom.archiveThread}
        onDelete={loom.deleteThread}
        onFork={loom.forkThread}
        onViewChange={loom.setThreadView}
      />

      <section className="workspace">
        <header className="thread-header">
          <div className="thread-heading">
            <strong>{thread?.title || (loom.connection === "connecting" ? "Starting Loom…" : "New conversation")}</strong>
            <span>{thread?.workspace || loom.runtime.defaultWorkspace || "Local workspace"}</span>
          </div>
          <div className="thread-header-actions">
            {running ? <span className="running-pill"><span className="status-dot live" />Working</span> : null}
            {archived ? <span className="running-pill">Archived · read only</span> : null}
            {!inspectorOpen ? (
              <button className="icon-button" onClick={() => setInspectorOpen(true)} title="Open runtime inspector"><PanelRightOpen size={17} /></button>
            ) : null}
          </div>
        </header>

        <div className={`conversation-stage ${running ? "is-running" : ""}`}>
          {running ? <RunProgress {...progressProps} placement="top" /> : null}
          <Transcript
            items={loom.items}
            running={running}
            promptDisabled={conversationDisabled}
            onPrompt={(prompt) => void loom.send(prompt)}
            onApproval={(item, approved) => void loom.respondApproval(item, approved)}
          />
        </div>

        <div className="composer-stage">
          {running ? <RunProgress {...progressProps} placement="bottom" /> : null}
          <Composer
            disabled={!thread || loom.connection !== "ready" || archived}
            running={running}
            model={loom.runtime.model}
            modelSnapshot={loom.models}
            modelBusy={loom.modelBusy}
            permissionMode={thread?.permissionMode || loom.runtime.defaultPermissionMode}
            permissionModes={loom.runtime.permissionModes}
            onPermissionModeChange={loom.setPermissionMode}
            onModelProfileChange={loom.switchModelProfile}
            onCustomModelChange={loom.switchCurrentModel}
            onAddModel={loom.addModel}
            onReasoningChange={loom.setReasoning}
            onSend={loom.send}
            onInterrupt={loom.interrupt}
          />
        </div>
      </section>

      {inspectorOpen ? <Inspector items={loom.items} onClose={() => setInspectorOpen(false)} /> : null}
    </div>
  );
}

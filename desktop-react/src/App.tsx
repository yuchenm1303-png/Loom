import { PanelRightOpen, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { Sidebar } from "./components/Sidebar";
import { Transcript } from "./components/Transcript";
import { useLoom } from "./state/useLoom";

export default function App() {
  const loom = useLoom();
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const thread = loom.active?.thread;
  const running = thread?.status === "running" || thread?.status === "waiting_approval";

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
        onOpen={(threadId) => void loom.openThread(threadId)}
        onNew={() => void loom.newThread()}
      />

      <section className="workspace">
        <header className="thread-header">
          <div className="thread-heading">
            <strong>{thread?.title || (loom.connection === "connecting" ? "Starting Loom…" : "New conversation")}</strong>
            <span>{thread?.workspace || loom.runtime.defaultWorkspace || "Local workspace"}</span>
          </div>
          <div className="thread-header-actions">
            {running ? <span className="running-pill"><span className="status-dot live" />Working</span> : null}
            {!inspectorOpen ? (
              <button className="icon-button" onClick={() => setInspectorOpen(true)} title="Open runtime inspector"><PanelRightOpen size={17} /></button>
            ) : null}
          </div>
        </header>

        <Transcript items={loom.items} onApproval={(item, approved) => void loom.respondApproval(item, approved)} />

        <Composer
          disabled={!thread || loom.connection !== "ready"}
          running={running}
          model={loom.runtime.model}
          permissionMode={thread?.permissionMode || loom.runtime.defaultPermissionMode}
          onSend={loom.send}
          onInterrupt={loom.interrupt}
        />
      </section>

      {inspectorOpen ? <Inspector items={loom.items} onClose={() => setInspectorOpen(false)} /> : null}
    </div>
  );
}

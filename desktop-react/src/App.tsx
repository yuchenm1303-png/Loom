import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { RunProgress } from "./components/RunProgress";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { ThreadHeader } from "./components/ThreadHeader";
import { Transcript } from "./components/Transcript";
import "./components/inline-thinking.css";
import { useI18n } from "./i18n";
import { useLoom } from "./state/useLoom";
import type { TranscriptItem } from "./types/loom";

const RESOLVED_APPROVAL_STATUSES = new Set([
  "approved",
  "denied",
  "completed",
  "cancelled",
  "interrupted",
  "failed",
]);

function isResolvedApproval(item: TranscriptItem): boolean {
  if (item.type !== "approval") return false;
  return RESOLVED_APPROVAL_STATUSES.has(String(item.status || "").toLowerCase());
}

export default function App() {
  const loom = useLoom();
  const { t } = useI18n();
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dismissedApprovalIds, setDismissedApprovalIds] = useState<Set<string>>(() => new Set());
  const thread = loom.active?.thread;
  const running = loom.turnActive || thread?.status === "running" || thread?.status === "waiting_approval";
  const archived = Boolean(thread?.archived);
  const conversationDisabled = !thread || loom.connection !== "ready" || running || archived;
  const threadTitle = thread?.title || (loom.connection === "connecting" ? t("app.startingLoom") : t("app.newConversation"));
  const workspace = thread?.workspace || loom.runtime.defaultWorkspace || "";
  const currentModel = loom.models?.current?.name || loom.models?.current?.model || loom.runtime.model;
  const permissionMode = thread?.permissionMode || loom.runtime.defaultPermissionMode;
  const capabilitySettings = loom.runtime.settings?.capabilities ?? {};
  const attachmentsEnabled = capabilitySettings.attachments !== false;
  const stickersEnabled = capabilitySettings.stickers !== false;

  useEffect(() => {
    setDismissedApprovalIds(new Set());
  }, [thread?.id]);

  const transcriptItems = loom.items.filter((item) => {
    if (item.type !== "approval") return true;
    if (isResolvedApproval(item)) return false;
    return !dismissedApprovalIds.has(item.id);
  });

  async function handleApproval(item: TranscriptItem, approved: boolean): Promise<void> {
    setDismissedApprovalIds((current) => {
      const next = new Set(current);
      next.add(item.id);
      return next;
    });

    try {
      await loom.respondApproval(item, approved);
    } catch (cause) {
      setDismissedApprovalIds((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
      console.error("Failed to respond to approval", cause);
    }
  }

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
          <h1>{t("app.serverDidNotStart")}</h1>
          <p>{loom.error || t("app.unknownConnectionError")}</p>
          <button className="button primary" onClick={() => window.location.reload()}><RotateCcw size={15} /> {t("app.retry")}</button>
        </div>
      </div>
    );
  }

  if (settingsOpen) {
    return (
      <SettingsPage
        runtime={loom.runtime}
        models={loom.models}
        running={Boolean(running)}
        onClose={() => setSettingsOpen(false)}
      />
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
        projects={loom.projects}
        projectsSupported={loom.projectsSupported}
        onAddProject={loom.createProject}
        onRenameProject={loom.renameProject}
        onRemoveProject={loom.removeProject}
        onRename={loom.renameThread}
        onArchive={loom.archiveThread}
        onDelete={loom.deleteThread}
        onFork={loom.forkThread}
        onViewChange={loom.setThreadView}
      />

      <section className="workspace">
        <ThreadHeader
          title={threadTitle}
          workspace={workspace}
          connection={loom.connection}
          status={thread?.status}
          running={running}
          archived={archived}
          model={currentModel}
          permissionMode={permissionMode}
          inspectorOpen={inspectorOpen}
          onOpenSettings={() => setSettingsOpen(true)}
          onToggleInspector={() => setInspectorOpen((open) => !open)}
        />

        <div className={`conversation-stage ${running ? "is-running" : ""}`}>
          {running ? <RunProgress {...progressProps} placement="top" /> : null}
          <Transcript
            items={transcriptItems}
            running={running}
            currentTurnId={thread?.currentTurnId}
            promptDisabled={conversationDisabled}
            onPrompt={(prompt) => void loom.send(prompt)}
            onApproval={handleApproval}
          />
        </div>

        <div className="composer-stage">
          <Composer
            disabled={!thread || loom.connection !== "ready" || archived}
            running={running}
            model={loom.runtime.model}
            modelSnapshot={loom.models}
            modelBusy={loom.modelBusy}
            permissionMode={permissionMode}
            permissionModes={loom.runtime.permissionModes}
            stickerPreferences={stickersEnabled ? loom.runtime.stickerPreferences : null}
            onPermissionModeChange={loom.setPermissionMode}
            onModelProfileChange={loom.switchModelProfile}
            onCustomModelChange={loom.switchCurrentModel}
            onAddModel={loom.addModel}
            onDeleteModel={loom.deleteModel}
            onReasoningChange={loom.setReasoning}
            onStickerPreferencesChange={async (preferences) => {
              await window.loom.call("sticker/preferences/set", { preferences });
            }}
            imagesAllowed={
              attachmentsEnabled && loom.runtime.attachments?.images !== false
            }
            onSend={loom.send}
            onInterrupt={loom.interrupt}
          />
        </div>
      </section>

      {inspectorOpen ? <Inspector items={loom.items} onClose={() => setInspectorOpen(false)} /> : null}
    </div>
  );
}

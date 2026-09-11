import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { LanguageSettingsDock } from "./components/LanguageSettingsDock";
import { RunProgress } from "./components/RunProgress";
import { SettingsComputerLogExport } from "./components/SettingsComputerLogExport";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { ThreadHeader } from "./components/ThreadHeader";
import { Transcript } from "./components/Transcript";
import "./components/inline-thinking.css";
import "./components/sidebar-codex-polish.css";
import "./components/shortcut-runtime.css";
import { useI18n } from "./i18n";
import {
  SHORTCUTS_CHANGED_EVENT,
  eventMatchesShortcut,
  readShortcutSettings,
  type ShortcutSettings,
} from "./keyboardShortcuts";
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

function afterPaint(callback: () => void): void {
  requestAnimationFrame(() => requestAnimationFrame(callback));
}

export default function App() {
  const loom = useLoom();
  const { t } = useI18n();
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [shortcuts, setShortcuts] = useState<ShortcutSettings>(() => readShortcutSettings());
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

  useEffect(() => {
    const syncShortcuts = () => setShortcuts(readShortcutSettings());
    window.addEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
    return () => window.removeEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
  }, []);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target?.closest(".shortcut-recorder")) return;

      const consume = () => {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
      };

      if (eventMatchesShortcut(event, shortcuts.openSettings)) {
        consume();
        setSettingsOpen((open) => !open);
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.stopTask) && running) {
        consume();
        void loom.interrupt();
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.newConversation)) {
        consume();
        setSettingsOpen(false);
        setSidebarOpen(true);
        void loom.newThread();
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.searchConversations)) {
        consume();
        setSettingsOpen(false);
        setSidebarOpen(true);
        afterPaint(() => {
          const input = document.querySelector<HTMLInputElement>(".compact-search.open input");
          if (input) {
            input.focus();
            input.select();
            return;
          }
          document.querySelector<HTMLButtonElement>('button[aria-label="Search conversations"]')?.click();
        });
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.focusComposer)) {
        consume();
        setSettingsOpen(false);
        afterPaint(() => document.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus());
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.attachFiles)) {
        consume();
        setSettingsOpen(false);
        afterPaint(() => document.querySelector<HTMLButtonElement>('.composer-tool[title^="Attach files"]')?.click());
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.toggleSidebar)) {
        consume();
        setSidebarOpen((open) => !open);
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.toggleInspector)) {
        consume();
        setInspectorOpen((open) => !open);
        return;
      }

      // Sidebar historically owned these two defaults. Once customized, block
      // the legacy handlers so the old binding does not remain active as a
      // hidden second shortcut.
      if (
        (eventMatchesShortcut(event, "Ctrl+N") && shortcuts.newConversation !== "Ctrl+N")
        || (eventMatchesShortcut(event, "Ctrl+K") && shortcuts.searchConversations !== "Ctrl+K")
      ) {
        consume();
      }
    };

    window.addEventListener("keydown", handleShortcut, true);
    return () => window.removeEventListener("keydown", handleShortcut, true);
  }, [loom, running, shortcuts]);

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

  async function handleMoveProject(projectId: string): Promise<void> {
    if (!thread?.id || running) return;
    await window.loom.call("thread/move_project", {
      threadId: thread.id,
      projectId,
    });
    await loom.refreshProjects();
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
      <>
        <SettingsPage
          runtime={loom.runtime}
          models={loom.models}
          running={Boolean(running)}
          onClose={() => setSettingsOpen(false)}
        />
        <SettingsComputerLogExport />
        <LanguageSettingsDock />
      </>
    );
  }

  return (
    <div className={`app-shell ${inspectorOpen ? "with-inspector" : ""} ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      {sidebarOpen ? (
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
      ) : null}

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
          projects={loom.projects}
          projectsSupported={loom.projectsSupported}
          currentProjectId={thread?.projectId}
          projectMoveDisabled={!thread || running}
          onMoveProject={handleMoveProject}
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

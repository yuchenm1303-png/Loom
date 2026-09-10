import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { ComputerUseHudOverlay } from "./components/ComputerUseHudOverlay";
import { Inspector } from "./components/Inspector";
import { LanguageSettingsDock } from "./components/LanguageSettingsDock";
import { RunProgress } from "./components/RunProgress";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { ThreadHeader } from "./components/ThreadHeader";
import { Transcript } from "./components/Transcript";
import "./components/inline-thinking.css";
import "./components/sidebar-codex-polish.css";
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

const COLLAPSED_PROJECTS_STORAGE_KEY = "loom.sidebar.collapsedProjects";

function isResolvedApproval(item: TranscriptItem): boolean {
  if (item.type !== "approval") return false;
  return RESOLVED_APPROVAL_STATUSES.has(String(item.status || "").toLowerCase());
}

function readCollapsedProjects(): Set<string> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(COLLAPSED_PROJECTS_STORAGE_KEY) || "[]") as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((value): value is string => typeof value === "string" && Boolean(value)));
  } catch {
    return new Set();
  }
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

  useEffect(() => {
    if (settingsOpen) return;
    const collapsedProjects = readCollapsedProjects();

    const projectKey = (button: HTMLButtonElement): string => button.getAttribute("title") || button.textContent?.trim() || "";

    const applyProjectState = () => {
      document.querySelectorAll<HTMLButtonElement>(".project-group-main").forEach((button) => {
        const group = button.closest<HTMLElement>(".project-group");
        if (!group) return;
        const key = projectKey(button);
        const collapsed = Boolean(key && collapsedProjects.has(key));
        group.classList.toggle("is-collapsed", collapsed);
        button.setAttribute("aria-expanded", String(!collapsed));
        button.setAttribute("aria-label", `${collapsed ? "展开" : "折叠"}项目 ${button.querySelector("span")?.textContent?.trim() || ""}`.trim());
      });
    };

    const handleProjectClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const button = target?.closest<HTMLButtonElement>(".project-group-main");
      if (!button) return;

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();

      const group = button.closest<HTMLElement>(".project-group");
      if (!group) return;
      const key = projectKey(button);
      const collapsed = !group.classList.contains("is-collapsed");
      group.classList.toggle("is-collapsed", collapsed);
      button.setAttribute("aria-expanded", String(!collapsed));
      button.setAttribute("aria-label", `${collapsed ? "展开" : "折叠"}项目 ${button.querySelector("span")?.textContent?.trim() || ""}`.trim());

      if (key) {
        if (collapsed) collapsedProjects.add(key);
        else collapsedProjects.delete(key);
        try {
          window.localStorage.setItem(COLLAPSED_PROJECTS_STORAGE_KEY, JSON.stringify([...collapsedProjects]));
        } catch {
          // The visual toggle still works for this renderer session.
        }
      }
    };

    applyProjectState();
    document.addEventListener("click", handleProjectClick, true);
    const observer = new MutationObserver(applyProjectState);
    const sidebar = document.querySelector(".compact-sidebar");
    if (sidebar) observer.observe(sidebar, { childList: true, subtree: true });

    return () => {
      document.removeEventListener("click", handleProjectClick, true);
      observer.disconnect();
    };
  }, [settingsOpen, loom.projects.length, loom.threads.length]);

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
      <>
        <div className="boot-error">
          <div className="boot-error-card">
            <div className="brand-mark large">L</div>
            <h1>{t("app.serverDidNotStart")}</h1>
            <p>{loom.error || t("app.unknownConnectionError")}</p>
            <button className="button primary" onClick={() => window.location.reload()}><RotateCcw size={15} /> {t("app.retry")}</button>
          </div>
        </div>
        <ComputerUseHudOverlay />
      </>
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
        <LanguageSettingsDock />
        <ComputerUseHudOverlay />
      </>
    );
  }

  return (
    <>
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
      <ComputerUseHudOverlay />
    </>
  );
}

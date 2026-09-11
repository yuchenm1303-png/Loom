import { RotateCcw } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { LanguageSettingsDock } from "./components/LanguageSettingsDock";
import { ReviewInteractionBridge } from "./components/ReviewInteractionBridge";
import { ReviewWorkspace } from "./components/ReviewWorkspace";
import { RunProgress } from "./components/RunProgress";
import { SettingsComputerLogExport } from "./components/SettingsComputerLogExport";
import { SettingsMemoryBridge } from "./components/SettingsMemoryBridge";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { ThreadHeader } from "./components/ThreadHeader";
import { Transcript } from "./components/Transcript";
import { TranscriptScrollController } from "./components/TranscriptScrollController";
import "./components/inline-thinking.css";
import "./components/review-dock.css";
import "./components/sidebar-codex-polish.css";
import "./components/shortcut-runtime.css";
import "./components/workspace-panels.css";
import { useI18n } from "./i18n";
import {
  SHORTCUTS_CHANGED_EVENT,
  eventMatchesShortcut,
  readShortcutSettings,
  type ShortcutSettings,
} from "./keyboardShortcuts";
import { useLoom } from "./state/useLoom";
import type { ThreadRecord, TranscriptItem } from "./types/loom";

const RESOLVED_APPROVAL_STATUSES = new Set([
  "approved",
  "denied",
  "completed",
  "cancelled",
  "interrupted",
  "failed",
]);

const SIDEBAR_WIDTH_KEY = "loom.layout.sidebarWidth";
const INSPECTOR_WIDTH_KEY = "loom.layout.inspectorWidth";
const SIDEBAR_MIN = 210;
const SIDEBAR_MAX = 420;
const INSPECTOR_MIN = 280;
const INSPECTOR_MAX = 520;
const MIN_WORKSPACE_WIDTH = 520;
const EMPTY_TRANSCRIPT_ITEMS: TranscriptItem[] = [];

type ResizePanel = "sidebar" | "inspector";
type LayoutStyle = CSSProperties & {
  "--loom-sidebar-panel-size": string;
  "--loom-inspector-panel-size": string;
};

type ResizeSession = {
  panel: ResizePanel;
  pointerId: number;
  startX: number;
  startWidth: number;
  currentWidth: number;
  minWidth: number;
  maxWidth: number;
  pendingDelta: number;
  frame: number | null;
  handle: HTMLDivElement;
};

function isResolvedApproval(item: TranscriptItem): boolean {
  if (item.type !== "approval") return false;
  return RESOLVED_APPROVAL_STATUSES.has(String(item.status || "").toLowerCase());
}

function afterPaint(callback: () => void): void {
  requestAnimationFrame(() => requestAnimationFrame(callback));
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function normalizeReviewPath(value: string | undefined): string {
  return String(value ?? "")
    .trim()
    .replaceAll("\\", "/")
    .replace(/^\.\//, "");
}

function reviewFileCount(items: TranscriptItem[]): number {
  const keys = new Set<string>();
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const paths = item.paths ?? [];
    if (paths.length) {
      for (const path of paths) {
        const normalized = String(path || "").trim().replaceAll("\\", "/");
        if (normalized) keys.add(normalized);
      }
    } else if (String(item.diff || "").trim()) {
      keys.add(item.id);
    }
  }
  return keys.size;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

function rootPixelWidth(name: "--sidebar-width" | "--inspector-width", fallback: number): number {
  try {
    const value = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function readPanelWidth(key: string, fallback: number, minimum: number, maximum: number): number {
  try {
    const stored = Number(window.localStorage.getItem(key));
    return Number.isFinite(stored) ? clamp(stored, minimum, maximum) : fallback;
  } catch {
    return fallback;
  }
}

function persistPanelWidth(key: string, value: number): void {
  try {
    window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    // The panel remains resizable for this session if storage is unavailable.
  }
}

function clearPanelWidth(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore storage failures and still restore the visual preset.
  }
}

export default function App() {
  const loom = useLoom();
  const { t } = useI18n();
  const shellRef = useRef<HTMLDivElement | null>(null);
  const resizeRef = useRef<ResizeSession | null>(null);
  const resizeReleaseFrameRef = useRef<number | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => readPanelWidth(
    SIDEBAR_WIDTH_KEY,
    rootPixelWidth("--sidebar-width", 252),
    SIDEBAR_MIN,
    SIDEBAR_MAX,
  ));
  const [inspectorWidth, setInspectorWidth] = useState(() => readPanelWidth(
    INSPECTOR_WIDTH_KEY,
    rootPixelWidth("--inspector-width", 316),
    INSPECTOR_MIN,
    INSPECTOR_MAX,
  ));
  const [resizingPanel, setResizingPanel] = useState<ResizePanel | null>(null);
  const [shortcuts, setShortcuts] = useState<ShortcutSettings>(() => readShortcutSettings());
  const [dismissedApprovalIds, setDismissedApprovalIds] = useState<Set<string>>(() => new Set());
  const thread = loom.active?.thread;
  const runtimeTurnRunning = thread?.status === "running" || thread?.status === "waiting_approval";
  const running = loom.turnActive || runtimeTurnRunning;
  const archived = Boolean(thread?.archived);
  const conversationDisabled = !thread || loom.connection !== "ready" || running || archived;
  const threadTitle = thread?.title || (loom.connection === "connecting" ? t("app.startingLoom") : t("app.newConversation"));
  const workspace = thread?.workspace || loom.runtime.defaultWorkspace || "";
  const currentModel = loom.models?.current?.name || loom.models?.current?.model || loom.runtime.model;
  const permissionMode = thread?.permissionMode || loom.runtime.defaultPermissionMode;
  const capabilitySettings = loom.runtime.settings?.capabilities ?? {};
  const attachmentsEnabled = capabilitySettings.attachments !== false;
  const stickersEnabled = capabilitySettings.stickers !== false;
  const changedFileCount = reviewFileCount(loom.items);
  const inspectorVisible = inspectorOpen && !reviewOpen;

  function focusReviewFile(path?: string): void {
    const normalized = normalizeReviewPath(path);
    setInspectorOpen(false);
    setReviewOpen(true);
    if (!normalized) return;

    afterPaint(() => {
      const candidates = Array.from(document.querySelectorAll<HTMLButtonElement>(".review-directory-files button[title]"));
      const exact = candidates.find((button) => normalizeReviewPath(button.getAttribute("title") || "") === normalized);
      const suffix = exact ?? candidates.find((button) => {
        const candidate = normalizeReviewPath(button.getAttribute("title") || "");
        return Boolean(candidate && (candidate.endsWith(`/${normalized}`) || normalized.endsWith(`/${candidate}`)));
      });
      suffix?.click();
    });
  }

  function toggleReview(): void {
    if (reviewOpen) {
      setReviewOpen(false);
      return;
    }
    focusReviewFile();
  }

  function toggleInspector(): void {
    setReviewOpen(false);
    setInspectorOpen((open) => !open);
  }

  useEffect(() => {
    setDismissedApprovalIds(new Set());
    setReviewOpen(false);
  }, [thread?.id]);

  useEffect(() => {
    const syncShortcuts = () => setShortcuts(readShortcutSettings());
    window.addEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
    return () => window.removeEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
  }, []);

  useEffect(() => () => {
    const session = resizeRef.current;
    if (session?.frame !== null && session?.frame !== undefined) cancelAnimationFrame(session.frame);
    if (resizeReleaseFrameRef.current !== null) cancelAnimationFrame(resizeReleaseFrameRef.current);
    document.body.classList.remove("loom-panel-resizing");
  }, []);

  useEffect(() => {
    const reconcile = () => {
      const viewport = shellRef.current?.clientWidth || window.innerWidth;
      let nextSidebar = sidebarWidth;
      let nextInspector = inspectorWidth;

      if (sidebarOpen && inspectorVisible) {
        const minimumPanels = SIDEBAR_MIN + INSPECTOR_MIN;
        const availableForPanels = Math.max(minimumPanels, viewport - MIN_WORKSPACE_WIDTH);
        let excess = nextSidebar + nextInspector - availableForPanels;
        if (excess > 0) {
          const inspectorReduction = Math.min(excess, nextInspector - INSPECTOR_MIN);
          nextInspector -= inspectorReduction;
          excess -= inspectorReduction;
        }
        if (excess > 0) {
          nextSidebar -= Math.min(excess, nextSidebar - SIDEBAR_MIN);
        }
      } else if (sidebarOpen) {
        nextSidebar = clamp(nextSidebar, SIDEBAR_MIN, Math.min(SIDEBAR_MAX, viewport - MIN_WORKSPACE_WIDTH));
      } else if (inspectorVisible) {
        nextInspector = clamp(nextInspector, INSPECTOR_MIN, Math.min(INSPECTOR_MAX, viewport - MIN_WORKSPACE_WIDTH));
      }

      nextSidebar = Math.round(nextSidebar);
      nextInspector = Math.round(nextInspector);
      if (nextSidebar !== sidebarWidth) {
        setSidebarWidth(nextSidebar);
        persistPanelWidth(SIDEBAR_WIDTH_KEY, nextSidebar);
      }
      if (nextInspector !== inspectorWidth) {
        setInspectorWidth(nextInspector);
        persistPanelWidth(INSPECTOR_WIDTH_KEY, nextInspector);
      }
    };

    const frame = window.requestAnimationFrame(reconcile);
    window.addEventListener("resize", reconcile);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", reconcile);
    };
  }, [inspectorVisible, inspectorWidth, sidebarOpen, sidebarWidth]);

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
        toggleInspector();
        return;
      }

      if (
        (eventMatchesShortcut(event, "Ctrl+N") && shortcuts.newConversation !== "Ctrl+N")
        || (eventMatchesShortcut(event, "Ctrl+K") && shortcuts.searchConversations !== "Ctrl+K")
      ) {
        consume();
      }
    };

    window.addEventListener("keydown", handleShortcut, true);
    return () => window.removeEventListener("keydown", handleShortcut, true);
  }, [loom, reviewOpen, running, shortcuts]);

  const transcriptItems = loom.items.filter((item) => {
    if (item.type !== "approval") return true;
    if (isResolvedApproval(item)) return false;
    return !dismissedApprovalIds.has(item.id);
  });
  const transcriptRunning = Boolean(
    runtimeTurnRunning
    && thread?.currentTurnId
    && transcriptItems.some((item) => item.turnId === thread.currentTurnId),
  );

  const panelMaximum = (panel: ResizePanel): number => {
    const viewport = shellRef.current?.clientWidth || window.innerWidth;
    const oppositeWidth = panel === "sidebar"
      ? (inspectorVisible ? inspectorWidth : 0)
      : (sidebarOpen ? sidebarWidth : 0);
    const hardMax = panel === "sidebar" ? SIDEBAR_MAX : INSPECTOR_MAX;
    const minimum = panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN;
    return Math.max(minimum, Math.min(hardMax, viewport - oppositeWidth - MIN_WORKSPACE_WIDTH));
  };

  const commitPanelWidth = (panel: ResizePanel, value: number, maximum = panelMaximum(panel)) => {
    const normalized = Math.round(clamp(
      value,
      panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN,
      maximum,
    ));
    if (panel === "sidebar") {
      setSidebarWidth(normalized);
      persistPanelWidth(SIDEBAR_WIDTH_KEY, normalized);
    } else {
      setInspectorWidth(normalized);
      persistPanelWidth(INSPECTOR_WIDTH_KEY, normalized);
    }
  };

  const resetPanelWidth = (panel: ResizePanel) => {
    const value = panel === "sidebar"
      ? rootPixelWidth("--sidebar-width", 252)
      : rootPixelWidth("--inspector-width", 316);
    clearPanelWidth(panel === "sidebar" ? SIDEBAR_WIDTH_KEY : INSPECTOR_WIDTH_KEY);
    commitPanelWidth(panel, value);
  };

  const startResize = (panel: ResizePanel, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    if (resizeReleaseFrameRef.current !== null) {
      cancelAnimationFrame(resizeReleaseFrameRef.current);
      resizeReleaseFrameRef.current = null;
    }

    const startWidth = panel === "sidebar" ? sidebarWidth : inspectorWidth;
    const minWidth = panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN;
    const maxWidth = panelMaximum(panel);
    resizeRef.current = {
      panel,
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth,
      currentWidth: startWidth,
      minWidth,
      maxWidth,
      pendingDelta: 0,
      frame: null,
      handle: event.currentTarget,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setResizingPanel(panel);
    document.body.classList.add("loom-panel-resizing");
    event.preventDefault();
  };

  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;

    const pointerDelta = event.clientX - session.startX;
    const requested = session.panel === "sidebar"
      ? session.startWidth + pointerDelta
      : session.startWidth - pointerDelta;
    const next = clamp(requested, session.minWidth, session.maxWidth);
    session.currentWidth = next;
    session.pendingDelta = session.panel === "sidebar"
      ? next - session.startWidth
      : session.startWidth - next;

    if (session.frame === null) {
      session.frame = requestAnimationFrame(() => {
        const current = resizeRef.current;
        if (!current || current !== session) return;
        current.frame = null;
        const base = current.panel === "sidebar" ? -50 : 50;
        current.handle.style.transform = `translateX(calc(${base}% + ${current.pendingDelta}px))`;
        current.handle.setAttribute("aria-valuenow", String(Math.round(current.currentWidth)));
      });
    }
    event.preventDefault();
  };

  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    resizeRef.current = null;

    if (session.frame !== null) cancelAnimationFrame(session.frame);
    session.handle.style.transform = "";
    session.handle.setAttribute("aria-valuenow", String(Math.round(session.currentWidth)));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    // Keep the shell in its no-transition resizing state while React commits the
    // final width. The nested rAF guarantees one paint at the settled geometry,
    // so the expensive transcript reflow happens once instead of animating over
    // ~240 ms of intermediate grid widths.
    commitPanelWidth(session.panel, session.currentWidth, session.maxWidth);
    resizeReleaseFrameRef.current = requestAnimationFrame(() => {
      resizeReleaseFrameRef.current = requestAnimationFrame(() => {
        resizeReleaseFrameRef.current = null;
        setResizingPanel(null);
        document.body.classList.remove("loom-panel-resizing");
        window.dispatchEvent(new Event("loom:panel-resize-end"));
      });
    });
  };

  const resizeWithKeyboard = (panel: ResizePanel, event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const amount = event.shiftKey ? 32 : 12;
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const current = panel === "sidebar" ? sidebarWidth : inspectorWidth;
    const delta = panel === "sidebar" ? direction * amount : -direction * amount;
    commitPanelWidth(panel, current + delta);
  };

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

  async function handleMoveProject(threadId: string, projectId: string): Promise<void> {
    const movingThread = loom.threads.find((entry) => entry.id === threadId);
    if (!movingThread) throw new Error("Conversation not found. Refresh the sidebar and try again.");
    if (movingThread.status === "running" || movingThread.status === "waiting_approval") {
      throw new Error("Stop the active task before moving this conversation to another project.");
    }

    const move = () => window.loom.call<{ thread: ThreadRecord }>("thread/move_project", {
      threadId,
      projectId,
    });

    let result: { thread: ThreadRecord };
    try {
      result = await move();
    } catch (cause) {
      const message = errorMessage(cause);
      if (!message.includes("Method not found: thread/move_project")) throw cause;
      await window.loom.disconnect();
      await window.loom.connect();
      result = await move();
    }

    const actualProjectId = String(result.thread?.projectId || "");
    if (actualProjectId !== projectId) {
      throw new Error("Loom did not persist the selected project. The conversation was left unchanged.");
    }

    await loom.refreshProjects();
    await loom.setThreadView(loom.threadView);
    if (thread?.id === threadId) await loom.openThread(threadId);
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
        <SettingsMemoryBridge threadId={thread?.id} running={Boolean(running)} />
        <SettingsComputerLogExport />
        <LanguageSettingsDock />
      </>
    );
  }

  const layoutStyle: LayoutStyle = {
    "--loom-sidebar-panel-size": `${sidebarWidth}px`,
    "--loom-inspector-panel-size": `${inspectorWidth}px`,
  };

  return (
    <div
      ref={shellRef}
      className={`app-shell workspace-panels ${sidebarOpen ? "sidebar-open" : "sidebar-closed"} ${inspectorVisible ? "inspector-open" : "inspector-closed"} ${reviewOpen ? "with-review" : ""} ${resizingPanel ? "is-resizing" : ""}`}
      style={layoutStyle}
    >
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
        onMoveProject={handleMoveProject}
        onRename={loom.renameThread}
        onArchive={loom.archiveThread}
        onDelete={loom.deleteThread}
        onFork={loom.forkThread}
        onViewChange={loom.setThreadView}
      />

      <div
        className="workspace-panel-resizer workspace-panel-resizer-left"
        role="separator"
        aria-label="Resize conversation sidebar"
        aria-orientation="vertical"
        aria-valuemin={SIDEBAR_MIN}
        aria-valuemax={panelMaximum("sidebar")}
        aria-valuenow={Math.round(sidebarWidth)}
        tabIndex={sidebarOpen ? 0 : -1}
        title="Drag to resize sidebar · Double-click to reset"
        onPointerDown={(event) => startResize("sidebar", event)}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) => resizeWithKeyboard("sidebar", event)}
        onDoubleClick={() => resetPanelWidth("sidebar")}
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
          sidebarOpen={sidebarOpen}
          inspectorOpen={inspectorVisible}
          reviewOpen={reviewOpen}
          reviewCount={changedFileCount}
          onOpenSettings={() => setSettingsOpen(true)}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          onToggleInspector={toggleInspector}
          onToggleReview={toggleReview}
        />

        <div className={`conversation-stage ${running ? "is-running" : ""}`}>
          {running ? <RunProgress {...progressProps} placement="top" /> : null}
          <Transcript
            items={transcriptItems}
            running={transcriptRunning}
            currentTurnId={thread?.currentTurnId}
            promptDisabled={conversationDisabled}
            onPrompt={(prompt) => void loom.send(prompt)}
            onApproval={handleApproval}
          />
          <TranscriptScrollController
            items={transcriptItems}
            threadId={thread?.id}
            currentTurnId={thread?.currentTurnId}
            running={running}
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
            imagesAllowed={attachmentsEnabled && loom.runtime.attachments?.images !== false}
            onSend={loom.send}
            onInterrupt={loom.interrupt}
          />
        </div>
      </section>

      <div
        className="workspace-panel-resizer workspace-panel-resizer-right"
        role="separator"
        aria-label="Resize runtime inspector"
        aria-orientation="vertical"
        aria-valuemin={INSPECTOR_MIN}
        aria-valuemax={panelMaximum("inspector")}
        aria-valuenow={Math.round(inspectorWidth)}
        tabIndex={inspectorVisible ? 0 : -1}
        title="Drag to resize inspector · Double-click to reset"
        onPointerDown={(event) => startResize("inspector", event)}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) => resizeWithKeyboard("inspector", event)}
        onDoubleClick={() => resetPanelWidth("inspector")}
      />

      <Inspector
        items={inspectorVisible ? loom.items : EMPTY_TRANSCRIPT_ITEMS}
        onClose={() => setInspectorOpen(false)}
      />
      <ReviewWorkspace items={loom.items} open={reviewOpen} onClose={() => setReviewOpen(false)} />
      <ReviewInteractionBridge onOpen={focusReviewFile} />
    </div>
  );
}

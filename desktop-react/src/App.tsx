import { RotateCcw } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { RunProgress } from "./components/RunProgress";
import { Sidebar } from "./components/Sidebar";
import { ThreadHeader } from "./components/ThreadHeader";
import { Transcript } from "./components/Transcript";
import "./components/inline-thinking.css";
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

const SIDEBAR_WIDTH_KEY = "loom.layout.sidebarWidth";
const INSPECTOR_WIDTH_KEY = "loom.layout.inspectorWidth";
const DEFAULT_SIDEBAR_WIDTH = 252;
const DEFAULT_INSPECTOR_WIDTH = 316;
const SIDEBAR_MIN = 210;
const SIDEBAR_MAX = 420;
const INSPECTOR_MIN = 280;
const INSPECTOR_MAX = 520;
const MIN_WORKSPACE_WIDTH = 480;

type ResizePanel = "sidebar" | "inspector";
type LayoutStyle = CSSProperties & {
  "--sidebar-panel-size": string;
  "--inspector-panel-size": string;
};

type ResizeSession = {
  panel: ResizePanel;
  pointerId: number;
  startX: number;
  startWidth: number;
  currentWidth: number;
};

function isResolvedApproval(item: TranscriptItem): boolean {
  if (item.type !== "approval") return false;
  return RESOLVED_APPROVAL_STATUSES.has(String(item.status || "").toLowerCase());
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
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
    // Layout remains usable for this session if storage is unavailable.
  }
}

export default function App() {
  const loom = useLoom();
  const shellRef = useRef<HTMLDivElement | null>(null);
  const resizeRef = useRef<ResizeSession | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    readPanelWidth(SIDEBAR_WIDTH_KEY, DEFAULT_SIDEBAR_WIDTH, SIDEBAR_MIN, SIDEBAR_MAX),
  );
  const [inspectorWidth, setInspectorWidth] = useState(() =>
    readPanelWidth(INSPECTOR_WIDTH_KEY, DEFAULT_INSPECTOR_WIDTH, INSPECTOR_MIN, INSPECTOR_MAX),
  );
  const [resizingPanel, setResizingPanel] = useState<ResizePanel | null>(null);
  const [dismissedApprovalIds, setDismissedApprovalIds] = useState<Set<string>>(() => new Set());
  const thread = loom.active?.thread;
  const running = loom.turnActive || thread?.status === "running" || thread?.status === "waiting_approval";
  const archived = Boolean(thread?.archived);
  const conversationDisabled = !thread || loom.connection !== "ready" || running || archived;
  const threadTitle = thread?.title || (loom.connection === "connecting" ? "Starting Loom…" : "New conversation");
  const workspace = thread?.workspace || loom.runtime.defaultWorkspace || "";
  const currentModel = loom.models?.current?.name || loom.models?.current?.model || loom.runtime.model;
  const permissionMode = thread?.permissionMode || loom.runtime.defaultPermissionMode;

  useEffect(() => {
    setDismissedApprovalIds(new Set());
  }, [thread?.id]);

  useEffect(() => () => {
    document.body.classList.remove("loom-panel-resizing");
  }, []);

  const transcriptItems = loom.items.filter((item) => {
    if (item.type !== "approval") return true;
    if (isResolvedApproval(item)) return false;
    return !dismissedApprovalIds.has(item.id);
  });

  const panelMaximum = (panel: ResizePanel): number => {
    const viewport = shellRef.current?.clientWidth || window.innerWidth;
    const oppositeWidth = panel === "sidebar"
      ? (inspectorOpen ? inspectorWidth : 0)
      : (sidebarOpen ? sidebarWidth : 0);
    const hardMax = panel === "sidebar" ? SIDEBAR_MAX : INSPECTOR_MAX;
    const minimum = panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN;
    return Math.max(minimum, Math.min(hardMax, viewport - oppositeWidth - MIN_WORKSPACE_WIDTH));
  };

  const writePanelCssWidth = (panel: ResizePanel, value: number) => {
    shellRef.current?.style.setProperty(
      panel === "sidebar" ? "--sidebar-panel-size" : "--inspector-panel-size",
      `${Math.round(value)}px`,
    );
  };

  const commitPanelWidth = (panel: ResizePanel, value: number) => {
    const normalized = Math.round(clamp(
      value,
      panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN,
      panelMaximum(panel),
    ));
    writePanelCssWidth(panel, normalized);
    if (panel === "sidebar") {
      setSidebarWidth(normalized);
      persistPanelWidth(SIDEBAR_WIDTH_KEY, normalized);
    } else {
      setInspectorWidth(normalized);
      persistPanelWidth(INSPECTOR_WIDTH_KEY, normalized);
    }
  };

  const startResize = (panel: ResizePanel, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const startWidth = panel === "sidebar" ? sidebarWidth : inspectorWidth;
    resizeRef.current = {
      panel,
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth,
      currentWidth: startWidth,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setResizingPanel(panel);
    document.body.classList.add("loom-panel-resizing");
    event.preventDefault();
  };

  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    const delta = event.clientX - session.startX;
    const requested = session.panel === "sidebar"
      ? session.startWidth + delta
      : session.startWidth - delta;
    const next = clamp(
      requested,
      session.panel === "sidebar" ? SIDEBAR_MIN : INSPECTOR_MIN,
      panelMaximum(session.panel),
    );
    session.currentWidth = next;
    writePanelCssWidth(session.panel, next);
  };

  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    resizeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    commitPanelWidth(session.panel, session.currentWidth);
    setResizingPanel(null);
    document.body.classList.remove("loom-panel-resizing");
  };

  const resizeWithKeyboard = (panel: ResizePanel, event: React.KeyboardEvent<HTMLDivElement>) => {
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

  const layoutStyle: LayoutStyle = {
    "--sidebar-panel-size": `${sidebarWidth}px`,
    "--inspector-panel-size": `${inspectorWidth}px`,
  };

  return (
    <div
      ref={shellRef}
      className={`app-shell panel-layout ${sidebarOpen ? "sidebar-open" : "sidebar-closed"} ${inspectorOpen ? "inspector-open" : "inspector-closed"} ${resizingPanel ? "is-resizing" : ""}`}
      style={layoutStyle}
    >
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

      <div
        className="panel-resizer panel-resizer-left"
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
        onDoubleClick={() => commitPanelWidth("sidebar", DEFAULT_SIDEBAR_WIDTH)}
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
          inspectorOpen={inspectorOpen}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          onToggleInspector={() => setInspectorOpen((open) => !open)}
        />

        <div className={`conversation-stage ${running ? "is-running" : ""}`}>
          {running ? <RunProgress {...progressProps} placement="top" /> : null}
          <Transcript
            items={transcriptItems}
            running={running}
            promptDisabled={conversationDisabled}
            onPrompt={(prompt) => void loom.send(prompt)}
            onApproval={handleApproval}
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
            permissionMode={permissionMode}
            permissionModes={loom.runtime.permissionModes}
            stickerPreferences={loom.runtime.stickerPreferences}
            onPermissionModeChange={loom.setPermissionMode}
            onModelProfileChange={loom.switchModelProfile}
            onCustomModelChange={loom.switchCurrentModel}
            onAddModel={loom.addModel}
            onReasoningChange={loom.setReasoning}
            onStickerPreferencesChange={async (preferences) => {
              await window.loom.call("sticker/preferences/set", { preferences });
            }}
            onSend={loom.send}
            onInterrupt={loom.interrupt}
          />
        </div>
      </section>

      <div
        className="panel-resizer panel-resizer-right"
        role="separator"
        aria-label="Resize runtime inspector"
        aria-orientation="vertical"
        aria-valuemin={INSPECTOR_MIN}
        aria-valuemax={panelMaximum("inspector")}
        aria-valuenow={Math.round(inspectorWidth)}
        tabIndex={inspectorOpen ? 0 : -1}
        title="Drag to resize inspector · Double-click to reset"
        onPointerDown={(event) => startResize("inspector", event)}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) => resizeWithKeyboard("inspector", event)}
        onDoubleClick={() => commitPanelWidth("inspector", DEFAULT_INSPECTOR_WIDTH)}
      />

      <Inspector items={loom.items} onClose={() => setInspectorOpen(false)} />
    </div>
  );
}

import { ArrowLeft, Check, GitFork, LoaderCircle, Reply, RotateCcw, X } from "./components/icons";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  lazy,
  Suspense,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { flushSync } from "react-dom";
import { AccountDialog } from "./components/AccountDialog";
import { ArtifactPreviewDock } from "./components/ArtifactPreviewDock";
import { Composer } from "./components/Composer";
import { BrowserWorkspace } from "./components/BrowserWorkspace";
import { browserPageUrl, type BrowserActivityTarget } from "./components/browserActivityModel";
import { Inspector } from "./components/Inspector";
import { LanguageSettingsDock } from "./components/LanguageSettingsDock";
import { ProfileInsightsPage } from "./components/ProfileInsightsPage";
import { ProjectDetailsPanel } from "./components/ProjectDetailsPanel";
import { FilesWorkbench } from "./components/FilesWorkbench";
import { ProjectGitBar } from "./components/ProjectGitBar";
import { ReviewInteractionBridge } from "./components/ReviewInteractionBridge";
import { ReviewWorkspace } from "./components/ReviewWorkspace";
import { RunProgress } from "./components/RunProgress";
import { LoomPet } from "./loomPetRuntime";
import { usePetEnabled } from "./petPreferences";
import { SettingsComputerLogExport } from "./components/SettingsComputerLogExport";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { SidebarControls, type SidebarControlsHandle } from "./components/SidebarControls";
import { SubAgentDock } from "./components/SubAgentDock";
import { ThreadHeader } from "./components/ThreadHeader";
import { RightToolDock } from "./components/RightToolDock";
import { BackgroundTaskPanel } from "./components/BackgroundTaskPanel";
import { OPEN_TERMINAL_EVENT, type TerminalRequest } from "./terminal";
import { TaskProgressPanel } from "./components/TaskProgressPanel";
import { ConversationRunsPanel } from "./components/ConversationRunsPanel";
import { SPLIT_DEFAULT, SPLIT_MAX, SPLIT_MIN, SplitConversationPane, SplitDropTarget } from "./components/SplitConversationPane";
import { startConversationActivity, useConversationAttention, watchConversations } from "./state/conversationActivity";
import { latestTaskPlan } from "./components/liveTaskProgress";
import { Transcript } from "./components/Transcript";
import { useLeavingItems } from "./components/leavingItems";
import { TranscriptScrollController } from "./components/TranscriptScrollController";
import "./components/inline-thinking.css";
import "./components/review-dock.css";
import "./components/shortcut-runtime.css";
import "./components/workspace-panels.css";
import "./components/panel-switching.css";
import "./components/thread-switch-transition.css";
import { canRenderArtifact } from "./artifactRenderers";
import { warmFontWeights } from "./fontWarmup";
import { useI18n } from "./i18n";
import { useMotionPresence } from "./motion/useMotionPresence";
import { DUR, EASE, RETAIN } from "./motion/tokens";
import { prefersReducedMotionNow, useReducedMotion } from "./motion/useReducedMotion";
import { RUN_STRIP_EXIT_MS } from "./presentationTiming";
import {
  SHORTCUTS_CHANGED_EVENT,
  eventMatchesShortcut,
  readShortcutSettings,
  type ShortcutSettings,
} from "./keyboardShortcuts";
import { useAccount } from "./state/useAccount";
import { currentTurnHasTerminalError, useLoom } from "./state/useLoom";
import type { ApprovalDecision, ThreadRecord, TranscriptItem } from "./types/loom";

const RESOLVED_APPROVAL_STATUSES = new Set([
  "approved",
  "denied",
  "completed",
  "cancelled",
  "interrupted",
  "failed",
]);

/** How long an answered approval stays to fold away (the log's fold: weave.css --wv-fold). */
const APPROVAL_EXIT_MS = RETAIN.fold;
const SIDEBAR_WIDTH_KEY = "loom.layout.sidebarWidth";
const INSPECTOR_WIDTH_KEY = "loom.layout.inspectorWidth";
const SIDEBAR_MIN = 210;
const SIDEBAR_MAX = 420;
const INSPECTOR_MIN = 280;
const INSPECTOR_MAX = 520;
const MIN_WORKSPACE_WIDTH = 520;
const PANEL_LAYOUT_COMMIT_EVENT = "loom:panel-layout-commit";
const SPLIT_RATIO_KEY = "loom.layout.splitRatio";

function readSplitRatio(): number {
  try {
    const saved = Number(localStorage.getItem(SPLIT_RATIO_KEY));
    if (Number.isFinite(saved) && saved > 0) return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, saved));
  } catch { /* The default share serves when storage is unavailable. */ }
  return SPLIT_DEFAULT;
}
const EMPTY_TRANSCRIPT_ITEMS: TranscriptItem[] = [];

type ResizePanel = "sidebar" | "inspector";
type LayoutMotionIntent =
  | "left-open"
  | "left-close"
  | "right-open"
  | "right-close"
  | "right-swap"
  | "neutral";
const TerminalPanel = lazy(() => import("./components/TerminalPanel"));
const ProjectMode = lazy(() => import("./components/ProjectMode").then((module) => ({ default: module.ProjectMode })));

type LayoutMotionAnchorKind =
  | "header-copy"
  | "header-actions"
  | "transcript"
  | "composer"
  | "composer-hint"
  | "run-progress";
type LayoutMotionAnchorSnapshot = {
  element: HTMLElement;
  selector: string;
  leftX: number;
  rightX: number;
  kind: LayoutMotionAnchorKind;
};

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

function renderableArtifactPaths(items: TranscriptItem[]): string[] {
  const paths: string[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    if (item.type !== "file_edit") continue;
    for (const rawPath of item.paths ?? []) {
      const normalized = String(rawPath || "").trim().replaceAll("\\", "/").replace(/^\.\//, "");
      if (!normalized || seen.has(normalized.toLowerCase()) || !canRenderArtifact(normalized)) continue;
      seen.add(normalized.toLowerCase());
      paths.push(normalized);
    }
  }
  return paths;
}

function subAgentCount(items: TranscriptItem[]): number {
  const agents = new Set<string>();
  let fallback = 0;

  for (const item of items) {
    if (item.type !== "tool_call" || String(item.toolName || "") !== "spawn_agent") continue;
    const result = item.result && typeof item.result === "object" && !Array.isArray(item.result)
      ? item.result as Record<string, unknown>
      : null;
    const sessionId = typeof result?.session_id === "string" ? result.session_id.trim() : "";
    const callId = String(item.callId || item.id || "").trim();
    if (sessionId || callId) agents.add(sessionId || callId);
    else fallback += 1;
  }

  return agents.size + fallback;
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

/*
 * Measure the elements the eye actually follows. The stage wrappers change
 * width when a side panel toggles; FLIP-ing those wrappers can put their
 * centered children briefly on the wrong side of the old position. Measuring
 * transcript/composer/header surfaces directly makes every transition
 * monotonic: old screen coordinate -> new screen coordinate, with no recoil.
 */
const PANEL_LAYOUT_ANCHORS: Array<{ selector: string; kind: LayoutMotionAnchorKind }> = [
  { selector: ".thread-header-copy", kind: "header-copy" },
  { selector: ".polished-thread-header-actions", kind: "header-actions" },
  { selector: ".transcript", kind: "transcript" },
  { selector: ".composer", kind: "composer" },
  { selector: ".composer-hint", kind: "composer-hint" },
  { selector: ".run-progress-inline", kind: "run-progress" },
];

function settleLayoutAnchorAnimations(root: HTMLElement | null): void {
  if (!root) return;
  for (const target of PANEL_LAYOUT_ANCHORS) {
    const element = root.querySelector<HTMLElement>(target.selector);
    if (!element) continue;
    for (const animation of element.getAnimations()) {
      if (animation.id !== "loom-layout-anchor") continue;
      try { animation.commitStyles(); } catch { /* Older Chromium fallback. */ }
      animation.cancel();
    }
  }
}

function clearLayoutAnchorStyles(root: HTMLElement | null): void {
  if (!root) return;
  for (const target of PANEL_LAYOUT_ANCHORS) {
    const element = root.querySelector<HTMLElement>(target.selector);
    if (!element) continue;
    element.style.removeProperty("translate");
    element.style.removeProperty("will-change");
  }
}

function captureLayoutAnchors(root: HTMLElement | null): LayoutMotionAnchorSnapshot[] {
  if (!root) return [];
  settleLayoutAnchorAnimations(root);

  const captures: LayoutMotionAnchorSnapshot[] = [];
  for (const target of PANEL_LAYOUT_ANCHORS) {
    const element = root.querySelector<HTMLElement>(target.selector);
    if (!element) continue;
    const rect = element.getBoundingClientRect();
    captures.push({
      element,
      selector: target.selector,
      leftX: rect.left,
      rightX: rect.right,
      kind: target.kind,
    });

    // Preserve the current painted point across rapid toggles, then remove the
    // committed transform before the real layout update. The next FLIP starts
    // from exactly where the element was visible, not from an old endpoint.
    element.style.removeProperty("translate");
    element.style.removeProperty("will-change");
  }
  return captures;
}

function anchorXForIntent(
  capture: LayoutMotionAnchorSnapshot,
  rect: DOMRect,
  intent: LayoutMotionIntent,
): { previous: number; next: number } {
  // When the right rail changes, the workspace's left edge is the invariant.
  // A fluid transcript can grow to the right without its text moving at all;
  // using its center would invent a fake leftward FLIP before the real move.
  if (intent === "right-open" || intent === "right-close" || intent === "right-swap") {
    return { previous: capture.leftX, next: rect.left };
  }

  // Mirror the rule for the left rail: the workspace's right edge is the
  // invariant, so width growth/shrinkage must not manufacture a false recoil.
  // Except for a column that fills a narrow workspace and grows as the sidebar
  // closes (a right-hand dock open, or a small window): its left edge carries
  // the eye, so the column glides from where it was and the width it gained
  // comes out from under the dock or the rail on the right, instead of its
  // left edge jumping the sidebar's width in one frame. (Shrinking as the
  // sidebar opens, it keeps the right edge: the left part goes under the card.)
  if (intent === "left-open" || intent === "left-close") {
    if (rect.width > capture.rightX - capture.leftX + 1) return { previous: capture.leftX, next: rect.left };
    return { previous: capture.rightX, next: rect.right };
  }

  return {
    previous: (capture.leftX + capture.rightX) / 2,
    next: (rect.left + rect.right) / 2,
  };
}

function animateLayoutAnchors(
  captures: LayoutMotionAnchorSnapshot[],
  intent: LayoutMotionIntent,
): Animation[] {
  const animations: Animation[] = [];
  for (const capture of captures) {
    if (!capture.element.isConnected) continue;
    const rect = capture.element.getBoundingClientRect();
    const anchor = anchorXForIntent(capture, rect, intent);
    const deltaX = anchor.previous - anchor.next;
    if (Math.abs(deltaX) < .5) continue;

    // The panel clock, which the docks settle on and the sidebar's toolbar travels on (sidebar-controls.css):
    // finishing the header/transcript/composer at 230–300ms made the main UI visibly arrive before the docks were
    // done settling.
    const duration = DUR.panel;

    // Animate the independent CSS translate property instead of transform.
    // Composer and other controls have their own transform effects; overriding
    // them during a panel toggle caused a second tiny "back step" on handoff.
    capture.element.style.willChange = "translate";
    const animation = capture.element.animate(
      [
        { translate: `${deltaX}px 0px` },
        { translate: "0px 0px" },
      ],
      {
        duration,
        easing: EASE.move,
        fill: "both",
      },
    );
    animation.id = "loom-layout-anchor";
    animations.push(animation);
  }
  return animations;
}


/** `value`, except while `hold` is on: then the value it had when the hold began. */
function useHeldWhile<T>(value: T, hold: boolean): T {
  const kept = useRef(value);
  if (!hold) kept.current = value;
  return hold ? kept.current : value;
}

export default function App() {
  useEffect(() => {
    const timer = window.setTimeout(() => window.dispatchEvent(new Event("loom:renderer-healthy")), 1000);
    return () => window.clearTimeout(timer);
  }, []);
  const petEnabled = usePetEnabled();
  const loom = useLoom();
  const account = useAccount();
  useEffect(() => {
    if (account.ready) void loom.refreshModels().catch(() => {});
  }, [account.ready, account.account.authenticated, account.account.user?.id, loom.refreshModels]);
  const { t, language } = useI18n();
  const shellRef = useRef<HTMLDivElement | null>(null);
  // The shell's width, kept by an observer. The separators' aria-valuemax is computed while rendering, and
  // clientWidth read there forces a layout in the middle of every streamed update.
  const shellWidthRef = useRef(0);
  useLayoutEffect(() => {
    const shell = shellRef.current;
    if (!shell || typeof ResizeObserver === "undefined") return;
    shellWidthRef.current = shell.clientWidth;
    const observer = new ResizeObserver(() => { shellWidthRef.current = shell.clientWidth; });
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);
  const resizeRef = useRef<ResizeSession | null>(null);
  const resizeReleaseFrameRef = useRef<number | null>(null);
  const layoutTransitionSerialRef = useRef(0);
  const reduceMotion = useReducedMotion();
  useLayoutEffect(() => {
    if (!reduceMotion) return;
    layoutTransitionSerialRef.current += 1;
    settleLayoutAnchorAnimations(shellRef.current);
    clearLayoutAnchorStyles(shellRef.current);
    delete document.documentElement.dataset.loomPanelHandoff;
    document.body.classList.remove("loom-panel-motion");
    window.dispatchEvent(new Event("loom:panel-resize-end"));
  }, [reduceMotion]);
  const [inspectorOpen, setInspectorOpen] = useState(() => {
    try { return localStorage.getItem("loom.inspector.open") === "true"; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("loom.inspector.open", String(inspectorOpen)); }
    catch { /* The panel remains usable when storage is unavailable. */ }
  }, [inspectorOpen]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarSearchOpen, setSidebarSearchOpen] = useState(false);
  const sidebarControlsRef = useRef<SidebarControlsHandle>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserActivityTarget, setBrowserActivityTarget] = useState<BrowserActivityTarget | null>(null);
  const browserAvailable = typeof window.loom?.embeddedBrowser === "function";
  const settingsPresence = useMotionPresence(settingsOpen, RETAIN.base);
  const [profileOpen, setProfileOpen] = useState(false);
  const profilePresence = useMotionPresence(profileOpen, RETAIN.base);
  // Settings and the profile page cover the shell. It goes inert (no focus, no pointer, out of the accessibility tree)
  // a painted frame after they arrive, not with them, and comes back as they start to leave: making the whole app
  // inert, or live again, re-styles every element in it, and a frame of the page would wait for that.
  const shellCovered = settingsPresence.mounted || profilePresence.mounted;
  const shellStaysCovered = settingsOpen || profileOpen;
  const [shellInert, setShellInert] = useState(false);
  useEffect(() => {
    if (!shellStaysCovered) {
      setShellInert(false);
      return;
    }
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setShellInert(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [shellStaysCovered]);
  const [accountOpen, setAccountOpen] = useState(false);
  useEffect(() => {
    const openProfile = () => {
      setSettingsOpen(false);
      setAccountOpen(false);
      setProfileOpen(true);
    };
    window.addEventListener("loom:open-profile", openProfile);
    return () => window.removeEventListener("loom:open-profile", openProfile);
  }, []);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [backgroundTasksOpen, setBackgroundTasksOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalMounted, setTerminalMounted] = useState(false);
  const [terminalRequest, setTerminalRequest] = useState<TerminalRequest | null>(null);
  const [backgroundRunningCount, setBackgroundRunningCount] = useState(0);
  const [taskPlanOpen, setTaskPlanOpen] = useState(false);
  const [runsOpen, setRunsOpen] = useState(false);
  const [splitThreadId, setSplitThreadId] = useState("");
  const [splitRatio, setSplitRatio] = useState(readSplitRatio);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [artifactPreview, setArtifactPreview] = useState<{ path: string; workspace: string } | null>(null);
  const autoOpenedAgentsForThreadRef = useRef("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [branchError, setBranchError] = useState("");
  const [reportingBranch, setReportingBranch] = useState(false);
  const [filesProjectId, setFilesProjectId] = useState("");
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
  const openComposerAccount = useCallback(() => setAccountOpen(true), []);
  useEffect(() => {
    const onStartBranch = (event: Event) => {
      const detail = (event as CustomEvent<{
        parentThreadId: string;
        title: string;
        prompt: string;
        summary?: string;
        acceptanceCriteria?: string[];
        sourceMessageId?: string;
        onSettled?: () => void;
      }>).detail;
      if (!detail?.parentThreadId || !detail.title?.trim() || !detail.prompt?.trim()) return;
      setBranchError("");
      void (async () => {
        try {
          const result = await window.loom.call<{ thread: ThreadRecord; started: boolean; error?: string }>(
            "thread/branch_create",
            {
              parentThreadId: detail.parentThreadId,
              title: detail.title,
              prompt: detail.prompt,
              summary: detail.summary || "",
              acceptanceCriteria: detail.acceptanceCriteria || [],
              sourceMessageId: detail.sourceMessageId || "",
              startNow: true,
            },
          );
          await loom.openThread(result.thread.id);
          if (result.error) setBranchError(t("branch.startFailed", { error: result.error }));
        } catch (cause) {
          setBranchError(cause instanceof Error ? cause.message : String(cause));
        } finally {
          detail.onSettled?.();
        }
      })();
    };
    window.addEventListener("loom:branch-task-start", onStartBranch);
    return () => window.removeEventListener("loom:branch-task-start", onStartBranch);
  }, [loom.openThread, t]);
  const thread = loom.active?.thread;
  const activeThreadId = thread?.id ?? "";
  const selectedThreadId = loom.openingThreadId || activeThreadId;
  const threadSwitching = Boolean(loom.openingThreadId && loom.openingThreadId !== activeThreadId);
  // While another conversation opens, the header's meter and the dock's counts keep what they showed (the meter
  // dimmed, the dock's tools disabled) until the new conversation's arrive. Dropping them to nothing made the meter
  // and the counts vanish and come back on every switch.
  const heldContext = useHeldWhile(loom.context, threadSwitching);
  const selectedThread = loom.threads.find(candidate => candidate.id === selectedThreadId) ?? thread;
  // What the other conversations are doing: one listener on the Host's stream, read by the conversations palette.
  useEffect(() => { if (loom.connection === "ready") startConversationActivity(); }, [loom.connection]);
  useEffect(() => { watchConversations([selectedThreadId, splitThreadId]); }, [selectedThreadId, splitThreadId]);
  const runsFinished = useConversationAttention();
  const runsLive = loom.threads.filter(entry => entry.id !== selectedThreadId && !entry.archived
    && (entry.status === "running" || entry.status === "waiting_approval")).length;
  // A conversation is shown once: opening the one in the split pane as the main one swaps the two.
  const openInMain = useCallback((threadId: string) => {
    if (!threadId || threadId === selectedThreadId) return;
    if (threadId === splitThreadId) setSplitThreadId(selectedThreadId);
    void loom.openThread(threadId);
  }, [loom.openThread, selectedThreadId, splitThreadId]);
  const openInSplit = useCallback((threadId: string) => {
    if (!threadId) return;
    if (threadId === selectedThreadId) return;
    setSplitThreadId(threadId);
  }, [selectedThreadId]);
  const changeSplitRatio = useCallback((ratio: number) => {
    setSplitRatio(ratio);
    try { localStorage.setItem(SPLIT_RATIO_KEY, String(ratio)); } catch { /* Kept until the next reload. */ }
  }, []);
  const resetSplitRatio = useCallback(() => {
    setSplitRatio(SPLIT_DEFAULT);
    try { localStorage.removeItem(SPLIT_RATIO_KEY); } catch { /* Nothing saved to clear. */ }
  }, []);
  // The split conversation cannot also be the main one (opened from the sidebar, say): the pane closes.
  useEffect(() => {
    if (splitThreadId && splitThreadId === selectedThreadId) setSplitThreadId("");
  }, [selectedThreadId, splitThreadId]);
  useEffect(() => {
    if (splitThreadId && loom.threads.length && !loom.threads.some(entry => entry.id === splitThreadId)) setSplitThreadId("");
  }, [loom.threads, splitThreadId]);
  const runtimeTurnRunning = thread?.status === "running" || thread?.status === "waiting_approval";
  // A terminal error ends an orphaned turn even when the last thread snapshot
  // still says running. Match the steering router so the model picker reappears.
  const running = (loom.turnActive || runtimeTurnRunning)
    && !currentTurnHasTerminalError(loom.items, thread?.currentTurnId);
  // The run strip floats over the transcript and says how a run ended before
  // it fades. Leaving the thread is not an ending: a strip that belonged to
  // another thread is never shown, even while it would still be exiting.
  const runStripThreadRef = useRef<string | undefined>(undefined);
  if (running) runStripThreadRef.current = thread?.id;
  const runStripPresence = useMotionPresence(Boolean(running), RUN_STRIP_EXIT_MS);
  const runStripVisible = runStripPresence.mounted && runStripThreadRef.current === thread?.id;
  // A run produces a main-thread frame per vsync, and every animation still going makes the browser resolve its
  // element again on each of them. Ornament that loops forever (the sidebar mark, the ambient glow) is frozen
  // by CSS for the length of a run; see html[data-loom-live] in sidebar.css and styles.css.
  useEffect(() => {
    const root = document.documentElement;
    if (running) root.dataset.loomLive = "true";
    else delete root.dataset.loomLive;
    return () => { delete root.dataset.loomLive; };
  }, [running]);
  // Once the app is idle, make the font instances the stylesheets use, so no surface pays for them on its first frame.
  useEffect(() => warmFontWeights(), []);
  const archived = Boolean(thread?.archived);
  const conversationDisabled = loom.connection !== "ready" || !account.account.authenticated || threadSwitching || running || archived;
  const connecting = loom.connection === "connecting";
  const threadTitle = selectedThread?.title || (connecting
    ? t(loom.startupStage === "library" ? "app.loadingConversations" : "app.startingLoom")
    : t("app.newConversation"));
  const workspace = thread?.workspace || loom.runtime.defaultWorkspace || "";
  const currentModel = loom.models?.current?.name || loom.models?.current?.model || loom.runtime.model;
  // Persistent-project mode is a per-conversation choice, remembered on this machine.
  const [projectModeIds, setProjectModeIds] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("loom.projectMode") || "[]"); } catch { return []; }
  });
  const projectMode = Boolean(thread?.id && projectModeIds.includes(thread.id));
  const setProjectMode = useCallback((mode: "chat" | "project") => {
    if (!thread?.id) return;
    const id = thread.id;
    setProjectModeIds((ids) => {
      const next = mode === "project" ? [...ids.filter((item) => item !== id), id].slice(-50) : ids.filter((item) => item !== id);
      try { localStorage.setItem("loom.projectMode", JSON.stringify(next)); } catch { /* private window */ }
      return next;
    });
  }, [thread?.id]);
  const permissionMode = thread?.permissionMode || loom.draftPermissionMode || loom.runtime.defaultPermissionMode;
  const capabilitySettings = loom.runtime.settings?.capabilities ?? {};
  const attachmentsEnabled = capabilitySettings.attachments !== false;
  const changedFileCount = useMemo(() => reviewFileCount(loom.items), [loom.items]);
  const renderableArtifacts = useMemo(() => renderableArtifactPaths(loom.items), [loom.items]);
  const artifactCount = renderableArtifacts.length;
  const agentCount = useMemo(() => subAgentCount(loom.items), [loom.items]);
  const heldCounts = useHeldWhile({ agents: agentCount, review: changedFileCount, artifacts: artifactCount }, threadSwitching);
  // The project line above the composer folds in and out between a project's conversation and one outside any
  // project (a new conversation): it used to come and go in one frame and move the conversation by its height.
  const currentProject = loom.projects.find((project) => project.id === thread?.projectId) ?? null;
  const gitBarPresence = useMotionPresence(Boolean(currentProject), RETAIN.foldBase);
  const lastGitBarProject = useRef(currentProject);
  if (currentProject) lastGitBarProject.current = currentProject;
  const gitBarProject = currentProject ?? lastGitBarProject.current;
  const taskMilestones = useMemo(() => latestTaskPlan(loom.items), [loom.items]);
  const taskCompleted = taskMilestones.filter(step => step.status === "completed").length;
  const selectedProject = selectedProjectId
    ? loom.projects.find((project) => project.id === selectedProjectId) ?? null
    : null;
  const filesProject = filesProjectId ? loom.projects.find((project) => project.id === filesProjectId) : undefined;
  // Files is a modal and leaves like every panel does: held mounted for its exit, still showing its project.
  const filesPresence = useMotionPresence(Boolean(filesProject), RETAIN.base);
  const lastFilesProjectRef = useRef(filesProject);
  if (filesProject) lastFilesProjectRef.current = filesProject;
  const filesShown = filesProject ?? (filesPresence.mounted ? lastFilesProjectRef.current : undefined);
  const dockProject = selectedProject
    ?? loom.projects.find(project => project.id === selectedThread?.projectId)
    ?? loom.projects.find(project => Boolean(project.root) &&
      project.root.replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase() ===
      workspace.replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase())
    ?? null;
  const projectDetailsOpen = Boolean(selectedProject);
  const artifactPreviewOpen = Boolean(artifactPreview);
  const browserDockRef = useRef(false);
  if (browserOpen || artifactPreviewOpen) browserDockRef.current = browserOpen;
  const browserDock = browserDockRef.current;
  useEffect(() => {
    if (browserOpen && (reviewOpen || agentsOpen || projectDetailsOpen || artifactPreviewOpen || filesProjectId || backgroundTasksOpen)) {
      setBrowserOpen(false);
    }
  }, [browserOpen, reviewOpen, agentsOpen, projectDetailsOpen, artifactPreviewOpen, filesProjectId, backgroundTasksOpen]);
  const inspectorVisible = inspectorOpen && !browserOpen && !reviewOpen && !projectDetailsOpen && !agentsOpen && !artifactPreviewOpen;

  const sidebarLayoutOpen = sidebarOpen;
  const inspectorLayoutOpen = inspectorVisible;
  const reviewLayoutOpen = reviewOpen;
  const agentsLayoutOpen = agentsOpen;
  const artifactLayoutOpen = artifactPreviewOpen || browserOpen;
  const projectDetailsLayoutOpen = projectDetailsOpen;
  const inspectorPresence = useMotionPresence(inspectorVisible, RETAIN.fast);
  const rightSurfaceOpen = inspectorVisible || reviewOpen || agentsOpen || artifactPreviewOpen || browserOpen || projectDetailsOpen;
  const rightOpenIntent: LayoutMotionIntent = rightSurfaceOpen ? "right-swap" : "right-open";

  const committedPanelLayoutRef = useRef({
    sidebarLayoutOpen,
    inspectorLayoutOpen,
    reviewLayoutOpen,
    agentsLayoutOpen,
    artifactLayoutOpen,
    projectDetailsLayoutOpen,
  });

  const runLayoutTransition = useCallback((
    update: () => void,
    intent: LayoutMotionIntent = "neutral",
  ) => {
    if (prefersReducedMotionNow()) {
      layoutTransitionSerialRef.current += 1;
      settleLayoutAnchorAnimations(shellRef.current);
      clearLayoutAnchorStyles(shellRef.current);
      delete document.documentElement.dataset.loomPanelHandoff;
      document.body.classList.remove("loom-panel-motion");
      flushSync(update);
      window.dispatchEvent(new Event("loom:panel-resize-end"));
      return;
    }

    const serial = layoutTransitionSerialRef.current + 1;
    layoutTransitionSerialRef.current = serial;

    // Nothing is snapshotted. Every dock runs its own transitions on the real element, so a second click turns it
    // around from where it is painted; the sidebar's toolbar glides between its two places (SidebarControls); and the
    // few live elements whose coordinates really change are carried by FLIP on the panel clock. A View Transition
    // froze the window for a frame or more before anything moved, re-styled the whole page twice, and in Electron drew
    // the toolbar's buttons fading out and popping back in.
    const captures = captureLayoutAnchors(shellRef.current);
    document.body.classList.add("loom-panel-motion");
    const docked = intent !== "neutral";
    if (intent === "right-open" || intent === "right-close" || intent === "right-swap") {
      document.documentElement.dataset.loomPanelHandoff = intent;
    } else {
      delete document.documentElement.dataset.loomPanelHandoff;
    }

    const finish = () => {
      if (layoutTransitionSerialRef.current !== serial) return;
      clearLayoutAnchorStyles(shellRef.current);
      delete document.documentElement.dataset.loomPanelHandoff;
      document.body.classList.remove("loom-panel-motion");
      window.dispatchEvent(new Event("loom:panel-resize-end"));
    };

    flushSync(update);
    const animations = animateLayoutAnchors(captures, intent);
    if (docked) {
      // A dock's own transitions run for the panel clock whether or not anything around it moves (a swap moves no
      // anchors), and a leaving dock stays hidden for its whole retained lifetime.
      void Promise.allSettled([
        ...animations.map((animation) => animation.finished),
        new Promise<void>((resolve) => window.setTimeout(resolve, RETAIN.panel)),
      ]).then(finish);
    } else if (!animations.length) {
      afterPaint(finish);
    } else {
      void Promise.allSettled(animations.map((animation) => animation.finished)).then(finish);
    }
  }, []);

  useLayoutEffect(() => {
    const previous = committedPanelLayoutRef.current;
    const nextLayout = {
      sidebarLayoutOpen,
      inspectorLayoutOpen,
      reviewLayoutOpen,
      agentsLayoutOpen,
      artifactLayoutOpen,
      projectDetailsLayoutOpen,
    };
    if (
      previous.sidebarLayoutOpen === nextLayout.sidebarLayoutOpen
      && previous.inspectorLayoutOpen === nextLayout.inspectorLayoutOpen
      && previous.reviewLayoutOpen === nextLayout.reviewLayoutOpen
      && previous.agentsLayoutOpen === nextLayout.agentsLayoutOpen
      && previous.artifactLayoutOpen === nextLayout.artifactLayoutOpen
      && previous.projectDetailsLayoutOpen === nextLayout.projectDetailsLayoutOpen
    ) return;
    committedPanelLayoutRef.current = nextLayout;
    window.dispatchEvent(new Event(PANEL_LAYOUT_COMMIT_EVENT));
  }, [
    agentsLayoutOpen,
    artifactLayoutOpen,
    inspectorLayoutOpen,
    projectDetailsLayoutOpen,
    reviewLayoutOpen,
    sidebarLayoutOpen,
  ]);

  const openArtifactPreview = useCallback((targetPath: string, targetWorkspace?: string) => {
    const nextPath = String(targetPath || "").trim();
    const nextWorkspace = String(targetWorkspace || workspace || "").trim();
    if (!nextWorkspace) return;

    runLayoutTransition(() => {
      setBrowserOpen(false);
      setBackgroundTasksOpen(false);
      setTaskPlanOpen(false);
      setReviewOpen(false);
      setAgentsOpen(false);
      setSelectedProjectId("");
      setInspectorOpen(false);
      setArtifactPreview({ path: nextPath, workspace: nextWorkspace });
    }, artifactPreviewOpen ? "neutral" : rightOpenIntent);
  }, [artifactPreviewOpen, rightOpenIntent, runLayoutTransition, workspace]);

  const closeArtifactPreview = useCallback(() => {
    runLayoutTransition(() => setArtifactPreview(null), "right-close");
  }, [runLayoutTransition]);

  const closeBrowser = useCallback(() => {
    runLayoutTransition(() => setBrowserOpen(false), "right-close");
  }, [runLayoutTransition]);

  function focusReviewFile(path?: string): void {
    const normalized = normalizeReviewPath(path);
    runLayoutTransition(() => {
      setBrowserOpen(false);
      setBackgroundTasksOpen(false);
      setTaskPlanOpen(false);
      setAgentsOpen(false);
      setArtifactPreview(null);
      setSelectedProjectId("");
      setInspectorOpen(false);
      setReviewOpen(true);
    }, reviewOpen ? "neutral" : rightOpenIntent);
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

  function openProjectDetails(projectId: string): void {
    runLayoutTransition(() => {
      setBrowserOpen(false);
      setBackgroundTasksOpen(false);
      setTaskPlanOpen(false);
      setReviewOpen(false);
      setAgentsOpen(false);
      setArtifactPreview(null);
      setInspectorOpen(false);
      setSelectedProjectId(projectId);
    }, projectDetailsOpen ? "neutral" : rightOpenIntent);
  }

  function closeProjectDetails(): void {
    runLayoutTransition(() => setSelectedProjectId(""), "right-close");
  }

  const openAgents = useCallback(() => {
    runLayoutTransition(() => {
      setBrowserOpen(false);
      setBackgroundTasksOpen(false);
      setTaskPlanOpen(false);
      setReviewOpen(false);
      setArtifactPreview(null);
      setInspectorOpen(false);
      setSelectedProjectId("");
      setAgentsOpen(true);
    }, agentsOpen ? "neutral" : rightOpenIntent);
  }, [agentsOpen, rightOpenIntent, runLayoutTransition]);

  function toggleAgents(): void {
    if (agentsOpen) {
      runLayoutTransition(() => setAgentsOpen(false), "right-close");
      return;
    }
    openAgents();
  }

  function toggleReview(): void {
    if (reviewOpen) {
      runLayoutTransition(() => setReviewOpen(false), "right-close");
      return;
    }
    focusReviewFile();
  }

  function toggleArtifacts(): void {
    if (artifactPreviewOpen) {
      closeArtifactPreview();
      return;
    }
    openArtifactPreview("", workspace);
  }

  function toggleInspector(): void {
    runLayoutTransition(() => {
      setBrowserOpen(false);
      setBackgroundTasksOpen(false);
      setTaskPlanOpen(false);
      setReviewOpen(false);
      setAgentsOpen(false);
      setArtifactPreview(null);
      setSelectedProjectId("");
      setInspectorOpen(!inspectorVisible);
    }, inspectorVisible ? "right-close" : rightOpenIntent);
  }

  function closeSidePanelsForPalette(): void {
    setBrowserOpen(false);
    setReviewOpen(false);
    setAgentsOpen(false);
    setArtifactPreview(null);
    setSelectedProjectId("");
    setInspectorOpen(false);
  }

  function toggleBackgroundTasks(): void {
    if (backgroundTasksOpen) {
      setBackgroundTasksOpen(false);
      return;
    }
    runLayoutTransition(() => {
      closeSidePanelsForPalette();
      setTaskPlanOpen(false);
      setBackgroundTasksOpen(true);
    }, rightSurfaceOpen ? "right-close" : "neutral");
  }

  function toggleTerminal(): void {
    if (terminalOpen) { setTerminalOpen(false); return; }
    runLayoutTransition(() => {
      closeSidePanelsForPalette();
      setBackgroundTasksOpen(false); setTaskPlanOpen(false); setRunsOpen(false);
      setTerminalMounted(true); setTerminalOpen(true);
    }, rightSurfaceOpen ? "right-close" : "neutral");
  }

  useEffect(() => {
    const receive = (event: Event) => {
      const request = (event as CustomEvent<TerminalRequest>).detail;
      if (!request?.workspace || request.workspace !== (selectedThread?.workspace || workspace)) return;
      runLayoutTransition(() => {
        closeSidePanelsForPalette(); setBackgroundTasksOpen(false); setTaskPlanOpen(false);
        setTerminalMounted(true); setTerminalRequest(request); setTerminalOpen(true);
      }, rightSurfaceOpen ? "right-close" : "neutral");
    };
    window.addEventListener(OPEN_TERMINAL_EVENT, receive);
    return () => window.removeEventListener(OPEN_TERMINAL_EVENT, receive);
  }, [selectedThread?.workspace, workspace, rightSurfaceOpen, runLayoutTransition]);

  useEffect(() => {
    if (rightSurfaceOpen || backgroundTasksOpen || taskPlanOpen || runsOpen || settingsOpen || profileOpen || accountOpen || filesProjectId) setTerminalOpen(false);
  }, [rightSurfaceOpen, backgroundTasksOpen, taskPlanOpen, runsOpen, settingsOpen, profileOpen, accountOpen, filesProjectId]);

  function toggleTaskPlan(): void {
    if (taskPlanOpen) {
      setTaskPlanOpen(false);
      return;
    }
    runLayoutTransition(() => {
      closeSidePanelsForPalette();
      setBackgroundTasksOpen(false);
      setRunsOpen(false);
      setTaskPlanOpen(true);
    }, rightSurfaceOpen ? "right-close" : "neutral");
  }

  // The conversations palette hangs on the same edge as the plan and the background tasks; it does not close the docks
  // (it is read while working), and it stays open across conversations so it can be used to hop between them.
  function toggleRuns(): void {
    if (runsOpen) { setRunsOpen(false); return; }
    setBackgroundTasksOpen(false);
    setTaskPlanOpen(false);
    setTerminalOpen(false);
    setRunsOpen(true);
  }

  useEffect(() => {
    if (!runsOpen) return;
    if (backgroundTasksOpen || taskPlanOpen || settingsOpen || profileOpen || accountOpen || filesProjectId) setRunsOpen(false);
  }, [accountOpen, backgroundTasksOpen, filesProjectId, profileOpen, runsOpen, settingsOpen, taskPlanOpen]);

  useEffect(() => {
    setDismissedApprovalIds(new Set());
    setReviewOpen(false);
    setAgentsOpen(false);
    setTaskPlanOpen(false);
    setBackgroundTasksOpen(false);
    setArtifactPreview(null);
    setTerminalOpen(false);
    autoOpenedAgentsForThreadRef.current = "";
  }, [thread?.id]);

  // Progress remains available in the dock; opening a thread or receiving plan
  // updates must not open a palette over the conversation.

  // Two palettes share the same edge, and a modal takes the whole window: the plan yields to both.
  useEffect(() => {
    if (!taskPlanOpen) return;
    if (backgroundTasksOpen || settingsOpen || profileOpen || accountOpen || filesProjectId) setTaskPlanOpen(false);
  }, [accountOpen, backgroundTasksOpen, filesProjectId, profileOpen, settingsOpen, taskPlanOpen]);

  useEffect(() => {
    if (settingsOpen || profileOpen || accountOpen || filesProjectId) setBackgroundTasksOpen(false);
  }, [accountOpen, filesProjectId, profileOpen, settingsOpen]);

  useEffect(() => {
    if (!taskMilestones.length) setTaskPlanOpen(false);
  }, [taskMilestones.length]);

  useEffect(() => {
    const openBrowserActivity = (event: Event) => {
      const target = (event as CustomEvent<BrowserActivityTarget>).detail;
      if (!browserAvailable || !target || !browserPageUrl(target.url)) return;
      runLayoutTransition(() => {
        setReviewOpen(false);
        setAgentsOpen(false);
        setArtifactPreview(null);
        setSelectedProjectId("");
        setFilesProjectId("");
        setBackgroundTasksOpen(false);
        setTaskPlanOpen(false);
        setInspectorOpen(false);
        setBrowserActivityTarget({ ...target });
        setBrowserOpen(true);
      }, rightOpenIntent);
    };
    window.addEventListener("loom:browser-activity-open", openBrowserActivity);
    // An AI task opened its own tab (already the active one): show it.
    const disposeReveal = window.loom?.onEmbeddedBrowserReveal?.(() => {
      if (!browserAvailable) return;
      runLayoutTransition(() => {
        setReviewOpen(false);
        setAgentsOpen(false);
        setArtifactPreview(null);
        setSelectedProjectId("");
        setFilesProjectId("");
        setBackgroundTasksOpen(false);
        setTaskPlanOpen(false);
        setInspectorOpen(false);
        setBrowserActivityTarget(null);
        setBrowserOpen(true);
      }, rightOpenIntent);
    });
    return () => {
      window.removeEventListener("loom:browser-activity-open", openBrowserActivity);
      disposeReveal?.();
    };
  }, [browserAvailable, runLayoutTransition, rightOpenIntent]);

  useEffect(() => {
    const handleArtifactPreview = (event: Event) => {
      const detail = (event as CustomEvent<{ path?: string; workspace?: string }>).detail;
      const targetPath = String(detail?.path || "").trim();
      if (!targetPath) return;
      openArtifactPreview(targetPath, detail?.workspace);
    };
    window.addEventListener("loom:artifact-preview-open", handleArtifactPreview);
    return () => window.removeEventListener("loom:artifact-preview-open", handleArtifactPreview);
  }, [openArtifactPreview]);

  useEffect(() => {
    const openFromActivity = () => openAgents();
    window.addEventListener("loom:sub-agents-open", openFromActivity);
    return () => window.removeEventListener("loom:sub-agents-open", openFromActivity);
  }, [openAgents]);

  useEffect(() => {
    const threadId = String(thread?.id || "");
    if (!threadId || !running || agentCount <= 0) return;
    if (autoOpenedAgentsForThreadRef.current === threadId) return;
    autoOpenedAgentsForThreadRef.current = threadId;
    const frame = window.requestAnimationFrame(() => openAgents());
    return () => window.cancelAnimationFrame(frame);
  }, [agentCount, openAgents, running, thread?.id]);

  useEffect(() => {
    if (!selectedProjectId) return;
    if (!loom.projects.some((project) => project.id === selectedProjectId)) setSelectedProjectId("");
  }, [loom.projects, selectedProjectId]);

  useEffect(() => {
    const syncShortcuts = () => setShortcuts(readShortcutSettings());
    window.addEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
    return () => window.removeEventListener(SHORTCUTS_CHANGED_EVENT, syncShortcuts);
  }, []);

  useEffect(() => () => {
    const session = resizeRef.current;
    if (session?.frame !== null && session?.frame !== undefined) cancelAnimationFrame(session.frame);
    if (resizeReleaseFrameRef.current !== null) cancelAnimationFrame(resizeReleaseFrameRef.current);
    layoutTransitionSerialRef.current += 1;
    settleLayoutAnchorAnimations(shellRef.current);
    clearLayoutAnchorStyles(shellRef.current);
    delete document.documentElement.dataset.loomPanelHandoff;
    document.body.classList.remove("loom-panel-resizing");
    document.body.classList.remove("loom-panel-motion");
  }, []);

  useEffect(() => {
    const reconcile = () => {
      const viewport = shellRef.current?.clientWidth || window.innerWidth;
      let nextSidebar = sidebarWidth;
      let nextInspector = inspectorWidth;

      if (sidebarLayoutOpen && inspectorLayoutOpen) {
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
      } else if (sidebarLayoutOpen) {
        nextSidebar = clamp(nextSidebar, SIDEBAR_MIN, Math.min(SIDEBAR_MAX, viewport - MIN_WORKSPACE_WIDTH));
      } else if (inspectorLayoutOpen) {
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
  }, [inspectorLayoutOpen, inspectorWidth, sidebarLayoutOpen, sidebarWidth]);

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

      if (eventMatchesShortcut(event, shortcuts.toggleTerminal)) {
        consume(); toggleTerminal(); return;
      }
      // Shell keystrokes, including Ctrl+C, Tab, arrows and Escape, belong to
      // the terminal rather than conversation shortcuts or agent interruption.
      if (target?.closest(".terminal-panel")) return;

      if (eventMatchesShortcut(event, shortcuts.stopTask) && running) {
        consume();
        void loom.interrupt();
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.newConversation)) {
        consume();
        setSettingsOpen(false);
        if (!sidebarOpen) runLayoutTransition(() => setSidebarOpen(true), "left-open");
        sidebarControlsRef.current?.write();
        void loom.newThread();
        return;
      }

      if (eventMatchesShortcut(event, shortcuts.searchConversations)) {
        consume();
        setSettingsOpen(false);
        if (!sidebarOpen) {
          runLayoutTransition(() => {
            setSidebarOpen(true);
            setSidebarSearchOpen(true);
          }, "left-open");
        } else if (!sidebarSearchOpen) {
          setSidebarSearchOpen(true);
        } else {
          // Already open: take the keyboard back to it, its query selected.
          const input = document.querySelector<HTMLInputElement>(".compact-search-shell.open input");
          input?.focus();
          input?.select();
        }
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
        runLayoutTransition(
          () => setSidebarOpen((open) => !open),
          sidebarOpen ? "left-close" : "left-open",
        );
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
  }, [loom, reviewOpen, runLayoutTransition, running, shortcuts, sidebarOpen, sidebarSearchOpen, terminalOpen, rightSurfaceOpen]);

  // An approval the reader answers folds away instead of taking its height back in one frame.
  const transcriptItems = useLeavingItems(
    loom.items,
    (item) => !isResolvedApproval(item) && !dismissedApprovalIds.has(item.id),
    (item) => item.type === "approval",
    reduceMotion ? 0 : APPROVAL_EXIT_MS,
    [dismissedApprovalIds],
  );
  // Only server-confirmed state may mark an existing turn live: before turn/started, currentTurnId still names the
  // previous (completed) turn, and calling that one live made it reflow and jump. The turn just sent is live from the
  // click all the same: it is the one that carries a message still "sending", and it names no current turn, so
  // nothing else can be taken for it. That holds before the conversation exists (a home send waits for thread/start),
  // which is why this does not read the app's own busy flag. Without it the new turn was drawn as finished until the
  // Host answered: a second or more with only the bubble on screen, no entrance, no "thinking".
  // Background session notifications redraw the shell/sidebar but must not
  // rescan thousands of frozen transcript items on every status update.
  const confirmedTurnRunning = useMemo(() => Boolean(
    runtimeTurnRunning
    && thread?.currentTurnId
    && transcriptItems.some((item) => item.turnId === thread.currentTurnId),
  ), [runtimeTurnRunning, thread?.currentTurnId, transcriptItems]);
  const transcriptRunning = useMemo(() => confirmedTurnRunning
    || transcriptItems.some((item) => item.type === "user_message" && item.status === "sending"),
  [confirmedTurnRunning, transcriptItems]);
  const visibleApproval = useMemo(() => transcriptItems.some((item) =>
    item.type === "approval" && !item.leaving && !isResolvedApproval(item)),
  [transcriptItems]);

  const panelMaximum = (panel: ResizePanel): number => {
    const viewport = shellWidthRef.current || shellRef.current?.clientWidth || window.innerWidth;
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

  const handleApproval = useCallback(async (item: TranscriptItem, decision: ApprovalDecision, feedback?: string): Promise<void> => {
    setDismissedApprovalIds((current) => {
      const next = new Set(current);
      next.add(item.id);
      return next;
    });

    try {
      await loom.respondApproval(item, decision, feedback);
    } catch (cause) {
      setDismissedApprovalIds((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
      console.error("Failed to respond to approval", cause);
    }
  }, [loom.respondApproval]);

  const handlePrompt = useCallback((prompt: string) => loom.send(prompt), [loom.send]);
  const handleOpenInsights = useCallback(() => {
    setSettingsOpen(false);
    setProfileOpen(true);
  }, []);
  const handleCloseInspector = useCallback(() => {
    runLayoutTransition(() => setInspectorOpen(false), "right-close");
  }, [runLayoutTransition]);

  async function handleMoveProject(threadId: string, projectId: string): Promise<void> {
    const movingThread = loom.threads.find((entry) => entry.id === threadId);
    if (!movingThread) throw new Error("找不到这个会话，请刷新侧栏后重试。");
    if (movingThread.status === "running" || movingThread.status === "waiting_approval") {
      throw new Error("当前任务运行中，结束后才能移动这个对话到项目。");
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
      throw new Error("Loom 没有保存项目归属，这个对话保持不变。");
    }

    await loom.refreshProjects();
    await loom.setThreadView(loom.threadView);
    if (thread?.id === threadId) await loom.openThread(threadId);
  }

  // The sidebar is memoized; these keep its props still across the renders that stream a turn.
  const latestProjectHandlers = useRef({ openProjectDetails, handleMoveProject });
  latestProjectHandlers.current = { openProjectDetails, handleMoveProject };
  const stableOpenProject = useCallback((projectId: string) => latestProjectHandlers.current.openProjectDetails(projectId), []);
  const stableMoveProject = useCallback((threadId: string, projectId: string) => latestProjectHandlers.current.handleMoveProject(threadId, projectId), []);
  // The sidebar's toolbar (SidebarControls): the toggle, and search, which opens the sidebar for it when it is closed.
  const toggleSidebar = useCallback(() => runLayoutTransition(
    () => setSidebarOpen((open) => !open),
    sidebarOpen ? "left-close" : "left-open",
  ), [runLayoutTransition, sidebarOpen]);
  const toggleSidebarSearch = useCallback(() => {
    if (sidebarOpen) {
      setSidebarSearchOpen((open) => !open);
      return;
    }
    runLayoutTransition(() => {
      setSidebarOpen(true);
      setSidebarSearchOpen(true);
    }, "left-open");
  }, [runLayoutTransition, sidebarOpen]);
  const startConversation = useCallback(() => loom.newThread(), [loom.newThread]);

  const progressProps = {
    modelActivity: loom.modelActivity,
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

  const layoutStyle: LayoutStyle = {
    "--loom-sidebar-panel-size": `${sidebarWidth}px`,
    "--loom-inspector-panel-size": `${inspectorWidth}px`,
  };

  return (
    <>
      <div
        ref={shellRef}
        className={`app-shell workspace-panels ${sidebarOpen ? "sidebar-open" : "sidebar-closed"} ${sidebarLayoutOpen ? "sidebar-layout-open" : "sidebar-layout-closed"} ${inspectorVisible ? "inspector-open" : "inspector-closed"} ${inspectorLayoutOpen ? "inspector-layout-open" : "inspector-layout-closed"} ${reviewLayoutOpen ? "with-review" : ""} ${agentsLayoutOpen ? "with-agents" : ""} ${artifactLayoutOpen ? "with-artifact-preview" : ""} ${projectDetailsLayoutOpen ? "with-project-details" : ""} ${splitThreadId ? "with-split" : ""} ${resizingPanel ? "is-resizing" : ""} ${shellCovered ? "is-settings-obscured" : ""}`}
        style={layoutStyle}
        inert={shellInert}
      >
      <RightToolDock
        hidden={settingsPresence.mounted || profilePresence.mounted || accountOpen || Boolean(filesProjectId)}
        loading={threadSwitching}
        hasWorkspace={Boolean(selectedThread?.workspace || workspace)}
        filesAvailable={Boolean(dockProject)}
        browserAvailable={browserAvailable}
        browserOpen={browserOpen}
        backgroundOpen={backgroundTasksOpen}
        terminalOpen={terminalOpen}
        backgroundRunningCount={backgroundRunningCount}
        taskOpen={taskPlanOpen}
        taskCompleted={taskCompleted}
        taskTotal={taskMilestones.length}
        taskRunning={running && taskMilestones.some(step => step.status === "in_progress")}
        runsOpen={runsOpen}
        runsLive={runsLive}
        runsFinished={runsFinished}
        onToggleRuns={toggleRuns}
        agentsOpen={agentsOpen}
        agentCount={heldCounts.agents}
        reviewOpen={reviewOpen}
        reviewCount={heldCounts.review}
        artifactsOpen={artifactPreviewOpen}
        artifactCount={heldCounts.artifacts}
        inspectorOpen={inspectorVisible}
        accountAuthenticated={account.account.authenticated}
        accountAvatar={account.account.user?.avatar_data_url?.trim() || undefined}
        shortcuts={{ settings: shortcuts.openSettings, inspector: shortcuts.toggleInspector, terminal: shortcuts.toggleTerminal }}
        onOpenFiles={() => {
          if (dockProject) setFilesProjectId(dockProject.id);
        }}
        onToggleBrowser={() => {
          if (!browserAvailable) return;
          if (browserOpen) {
            closeBrowser();
            return;
          }
          setBrowserActivityTarget(null);
          runLayoutTransition(() => {
            setReviewOpen(false);
            setAgentsOpen(false);
            setArtifactPreview(null);
            setSelectedProjectId("");
            setFilesProjectId("");
            setBackgroundTasksOpen(false);
            setTaskPlanOpen(false);
            setInspectorOpen(false);
            setBrowserOpen(true);
          }, rightOpenIntent);
        }}
        onToggleBackground={toggleBackgroundTasks}
        onToggleTerminal={toggleTerminal}
        onToggleTasks={toggleTaskPlan}
        onToggleAgents={toggleAgents}
        onToggleReview={toggleReview}
        onToggleArtifacts={toggleArtifacts}
        onToggleInspector={toggleInspector}
        onOpenProfile={() => {
          setSettingsOpen(false);
          setAccountOpen(false);
          setProfileOpen(true);
        }}
        onOpenAccount={() => setAccountOpen(true)}
        onOpenSettings={() => {
          setProfileOpen(false);
          setSettingsOpen(true);
        }}
      />
      <SidebarControls
        ref={sidebarControlsRef}
        open={sidebarOpen}
        searchOpen={sidebarOpen && sidebarSearchOpen}
        onToggle={toggleSidebar}
        onSearch={toggleSidebarSearch}
        onNew={startConversation}
      />
      <Sidebar
        threads={loom.threads}
        loading={connecting}
        activeId={selectedThreadId}
        activeProjectId={selectedProjectId}
        threadView={loom.threadView}
        archivedCount={loom.threadCounts.archived}
        onOpen={loom.openThread}
        onPrefetch={loom.prefetchThread}
        onNew={loom.newThread}
        onOpenProject={stableOpenProject}
        projects={loom.projects}
        projectsSupported={loom.projectsSupported}
        onAddProject={loom.createProject}
        onRenameProject={loom.renameProject}
        onRemoveProject={loom.removeProject}
        onMoveProject={stableMoveProject}
        onRename={loom.renameThread}
        onArchive={loom.archiveThread}
        onDelete={loom.deleteThread}
        onFork={loom.forkThread}
        onOpenSplit={openInSplit}
        onViewChange={loom.setThreadView}
        searchOpen={sidebarSearchOpen}
        onSearchOpenChange={setSidebarSearchOpen}
      />

      <div
        className="workspace-panel-resizer workspace-panel-resizer-left"
        role="separator"
        aria-label="Resize conversation sidebar"
        aria-orientation="vertical"
        aria-valuemin={SIDEBAR_MIN}
        aria-valuemax={panelMaximum("sidebar")}
        aria-valuenow={Math.round(sidebarWidth)}
        tabIndex={sidebarLayoutOpen && sidebarOpen ? 0 : -1}
        title="Drag to resize sidebar · Double-click to reset"
        onPointerDown={(event) => startResize("sidebar", event)}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) => resizeWithKeyboard("sidebar", event)}
        onDoubleClick={() => resetPanelWidth("sidebar")}
      />

      <section
        className={`workspace ${threadSwitching ? "is-thread-switching" : ""}`.trim()}
        style={splitThreadId ? { "--loom-split-ratio": splitRatio } as CSSProperties : undefined}
        aria-busy={threadSwitching ? true : undefined}
      >
        <ThreadHeader
          sidebarOpen={sidebarOpen}
          title={threadTitle}
          workspace={selectedThread?.workspace || workspace}
          connection={loom.connection}
          status={thread?.status}
          running={running}
          loading={threadSwitching}
          archived={archived}
          model={currentModel}
          permissionMode={permissionMode}
          context={heldContext}
          contextPending={threadSwitching}
          compacting={!threadSwitching && loom.compacting}
          compactionProgress={threadSwitching ? null : loom.compactionProgress}
          onCompactContext={() => void loom.compactContext()}
          mode={thread ? (projectMode ? "project" : "chat") : undefined}
          onModeChange={setProjectMode}
        />

        {terminalMounted && <Suspense fallback={null}><TerminalPanel
          workspace={selectedThread?.workspace || workspace}
          connected={loom.connection === "ready"}
          open={terminalOpen}
          request={terminalRequest}
          onClose={() => setTerminalOpen(false)}
        /></Suspense>}
        <BackgroundTaskPanel
          workspace={selectedThread?.workspace || workspace}
          connected={loom.connection === "ready"}
          open={backgroundTasksOpen && !browserOpen && !settingsOpen && !profileOpen && !accountOpen}
          onClose={() => setBackgroundTasksOpen(false)}
          onRunningCount={setBackgroundRunningCount}
        />

        <TaskProgressPanel
          steps={taskMilestones}
          open={taskPlanOpen}
          onClose={() => setTaskPlanOpen(false)}
        />
        <ConversationRunsPanel
          open={runsOpen}
          threads={loom.threads}
          projects={loom.projects}
          activeId={selectedThreadId}
          splitId={splitThreadId}
          onClose={() => setRunsOpen(false)}
          onOpen={openInMain}
          onSplit={openInSplit}
        />

        {projectMode && thread ? (
          <div className="project-mode-stage" inert={threadSwitching}>
            <Suspense fallback={null}>
              <ProjectMode key={thread.id} threadId={thread.id} workspace={selectedThread?.workspace || workspace} model={currentModel}
                           models={loom.models} onSwitchModel={loom.switchModelProfile} />
            </Suspense>
          </div>
        ) : (<>
        <div className={`conversation-stage ${running ? "is-running" : ""}`} inert={threadSwitching}>
          {runStripVisible ? <RunProgress {...progressProps} placement="top" motionPhase={runStripPresence.phase} /> : null}
          {thread?.parentThreadId ? (
            <div className="loom-branch-parent-strip" role="group" aria-label={t("branch.child")}>
              <span className="loom-branch-parent-mark"><GitFork size={17} /></span>
              <div className="loom-branch-parent-copy"><strong>{t("branch.child")}</strong><span>{t("branch.childHint")}</span></div>
              <button type="button" onClick={() => { setBranchError(""); void loom.openThread(thread.parentThreadId!); }}>
                <ArrowLeft size={14} />{t("branch.back")}
              </button>
              <button
                type="button"
                className="loom-branch-report"
                disabled={running || reportingBranch || Boolean(thread.branchReportedAt)}
                title={t(thread.branchReportedAt ? "branch.reported" : running ? "branch.wait" : "branch.reportHint")}
                onClick={() => {
                  if (!thread.parentThreadId) return;
                  setReportingBranch(true);
                  setBranchError("");
                  void (async () => {
                    try {
                      const report = await window.loom.call<{ parentThreadId: string }>("thread/branch_report", { threadId: thread.id });
                      await loom.openThread(report.parentThreadId);
                    } catch (cause) {
                      setBranchError(cause instanceof Error ? cause.message : String(cause));
                    } finally {
                      setReportingBranch(false);
                    }
                  })();
                }}
              >{thread.branchReportedAt ? <Check size={14} /> : reportingBranch ? <LoaderCircle size={14} className="loom-branch-spinner" /> : <Reply size={14} />}{t(thread.branchReportedAt ? "branch.reported" : reportingBranch ? "branch.reporting" : "branch.report")}</button>
            </div>
          ) : null}
          {branchError ? <div className="loom-branch-error" role="alert"><span>{branchError}</span><button type="button" aria-label={t("branch.dismissError")} onClick={() => setBranchError("")}><X size={14} /></button></div> : null}
          <Transcript
            threadId={thread?.id}
            runtimePhase={loom.modelActivity?.turnId === thread?.currentTurnId ? loom.modelActivity?.phase : undefined}
            items={transcriptItems}
            running={transcriptRunning}
            currentTurnId={confirmedTurnRunning ? thread?.currentTurnId : undefined}
            workspace={workspace}
            promptDisabled={conversationDisabled}
            onPrompt={handlePrompt}
            onApproval={handleApproval}
            onOpenInsights={handleOpenInsights}
            connecting={connecting}
            startupStage={loom.startupStage}
            usageReady={loom.connection === "ready" && !loom.openingThreadId}
          />
          <TranscriptScrollController
            items={transcriptItems}
            threadId={thread?.id}
            currentTurnId={thread?.currentTurnId}
            running={running}
            hasOlder={loom.hasOlderTurns}
            loadingOlder={loom.loadingOlderTurns}
            onLoadOlder={loom.loadOlderTurns}
          />
        </div>

        <div className="composer-stage" inert={threadSwitching}>
          {gitBarPresence.mounted && gitBarProject ? (
            <div className="project-git-fold" data-motion-phase={gitBarPresence.phase} inert={!currentProject}>
              <div className="project-git-fold-body">
                <ProjectGitBar project={gitBarProject} onOpen={() => openProjectDetails(gitBarProject.id)} onFiles={() => setFilesProjectId(gitBarProject.id)}/>
              </div>
            </div>
          ) : null}
          {petEnabled && thread && <LoomPet key={thread.id} running={running}
            approval={thread.status === "waiting_approval" || visibleApproval}
            completed={thread.status === "completed"} />}
          <Composer
            toolSelection={thread?.toolSelection}
            workspace={workspace}
            onOpenWorkspace={dockProject ? () => setFilesProjectId(dockProject.id) : undefined}
            threadId={thread?.id}
            disabled={loom.connection !== "ready" || archived}
            starting={connecting}
            running={running}
            model={loom.models?.current?.model || loom.runtime.model}
            modelSnapshot={loom.models}
            modelBusy={loom.modelBusy}
            accountAuthenticated={account.account.authenticated}
            accountUsageIdentity={account.account.authenticated ? String(account.account.user?.id || "") : "signed-out"}
            onOpenAccount={openComposerAccount}
            permissionMode={permissionMode}
            permissionModes={loom.runtime.permissionModes}
            onPermissionModeChange={loom.setPermissionMode}
            onModelProfileChange={loom.switchModelProfile}
            onCustomModelChange={loom.switchCurrentModel}
            onConfigureModelProvider={loom.configureModelProvider}
            onRefreshModels={loom.refreshModels}
            onRefreshActiveThread={loom.refreshActiveThreadState}
            onAddModel={loom.addModel}
            onDeleteModel={loom.deleteModel}
            onReasoningChange={loom.setReasoning}
            imagesAllowed={attachmentsEnabled && loom.models?.current?.vision !== false}
            imageSupport={loom.models?.current?.vision ?? null}
            onSend={loom.send}
            onInterrupt={loom.interrupt}
            paused={Boolean(thread?.paused)}
            onPausedChange={typeof thread?.paused === "boolean" ? loom.setPaused : undefined}
          />
        </div>
        </>)}
      </section>
      <SplitConversationPane
        threadId={splitThreadId}
        projects={loom.projects}
        ratio={splitRatio}
        onRatioChange={changeSplitRatio}
        onRatioReset={resetSplitRatio}
        onClose={() => setSplitThreadId("")}
        onPromote={openInMain}
      />
      <SplitDropTarget splitOpen={Boolean(splitThreadId)} onOpen={openInMain} onSplit={openInSplit} />

      <div
        className="workspace-panel-resizer workspace-panel-resizer-right"
        role="separator"
        aria-label="Resize runtime inspector"
        aria-orientation="vertical"
        aria-valuemin={INSPECTOR_MIN}
        aria-valuemax={panelMaximum("inspector")}
        aria-valuenow={Math.round(inspectorWidth)}
        tabIndex={inspectorLayoutOpen && inspectorVisible ? 0 : -1}
        title="Drag to resize inspector · Double-click to reset"
        onPointerDown={(event) => startResize("inspector", event)}
        onPointerMove={moveResize}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) => resizeWithKeyboard("inspector", event)}
        onDoubleClick={() => resetPanelWidth("inspector")}
      />

      <Inspector
        items={inspectorPresence.mounted ? loom.items : EMPTY_TRANSCRIPT_ITEMS}
        onClose={handleCloseInspector}
      />
      <ProjectDetailsPanel
        project={selectedProject}
        open={projectDetailsOpen}
        threads={loom.threads}
        activeThreadId={thread?.id}
        onClose={closeProjectDetails}
        onNewThread={(project) => loom.newThread(project.root || undefined, project.id)}
        onOpenThread={loom.openThread}
        onSetInstructions={loom.setProjectInstructions}
        onGitChanged={() => { void loom.refreshProjects(); }}
        onOpenFiles={() => { if (selectedProject) setFilesProjectId(selectedProject.id); }}
      />
      <ReviewWorkspace items={loom.items} open={reviewOpen} onClose={() => runLayoutTransition(() => setReviewOpen(false), "right-close")} />
      <SubAgentDock
        items={loom.items}
        open={agentsOpen}
        active={Boolean(running)}
        onClose={() => runLayoutTransition(() => setAgentsOpen(false), "right-close")}
      />
      <ArtifactPreviewDock
        items={loom.items}
        open={artifactPreviewOpen || browserOpen}
        path={artifactPreview?.path || ""}
        workspace={artifactPreview?.workspace || workspace}
        onClose={browserDock ? closeBrowser : closeArtifactPreview}
        ariaLabel={browserDock ? t("settings.browser.title") : undefined}
        content={browserDock && browserAvailable ? visible => <BrowserWorkspace
          target={browserActivityTarget}
          workspace={workspace}
          previewDisabled={Boolean(running)}
          onStartPreview={() => handlePrompt(language === "zh-CN" ? "请检查当前项目，启动合适的开发预览服务，并使用内置浏览器打开预览网址。复用已有服务，不要重复启动。" : "Inspect this project, start its development preview server, and open the preview URL in the embedded browser. Reuse an existing server when available.")}
          hidden={!visible || settingsPresence.mounted || profilePresence.mounted || accountOpen || reviewOpen || agentsOpen || projectDetailsOpen || artifactPreviewOpen || Boolean(filesProjectId) || backgroundTasksOpen}
          onClose={closeBrowser}
        /> : undefined}
      />
      <ReviewInteractionBridge onOpen={focusReviewFile} />
      <AccountDialog
        open={accountOpen}
        account={account.account}
        ready={account.ready}
        busy={account.busy}
        error={account.error}
        onClose={() => setAccountOpen(false)}
        onClearError={account.clearError}
        onRetry={account.refresh}
        onLogin={account.login}
        onRegister={account.register}
        capabilities={account.capabilities}
        onUpdateProfile={account.updateProfile}
        onOpenProfile={() => {
          setAccountOpen(false);
          setSettingsOpen(false);
          setProfileOpen(true);
        }}
        onLogout={account.logout}
      />
      </div>
      {filesShown ? <FilesWorkbench
        key={filesShown.id}
        projectId={filesShown.id}
        projectName={filesShown.name}
        root={filesShown.root}
        open={Boolean(filesProject)}
        phase={filesPresence.phase}
        onClose={() => setFilesProjectId("")}
        onChanged={() => { void loom.refreshProjects(); }}
      /> : null}

      {profilePresence.mounted ? (
        <div className="profile-insights-host" data-motion-phase={profilePresence.phase} inert={!profileOpen}>
          <ProfileInsightsPage
            account={account.account}
            onClose={() => setProfileOpen(false)}
          />
        </div>
      ) : null}

      {settingsPresence.mounted ? (
        <div className="settings-host" data-motion-phase={settingsPresence.phase} inert={!settingsOpen}>
          <SettingsPage
            runtime={loom.runtime}
            models={loom.models}
            threadId={thread?.id}
            running={Boolean(running)}
            onRefreshModels={loom.refreshModels}
            onOpenThread={async id => { await loom.openThread(id); setSettingsOpen(false); }}
            onClose={() => setSettingsOpen(false)}
          />
          <SettingsComputerLogExport />
          <LanguageSettingsDock />
        </div>
      ) : null}
    </>
  );
}

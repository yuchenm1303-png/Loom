import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AddModelInput,
  ContextCompactionProgress,
  ContextReport,
  InitializeResult,
  ModelRestartResult,
  ModelSnapshot,
  PendingApproval,
  ProjectListResult,
  ProjectRecord,
  ReasoningUpdateResult,
  ThreadReadResult,
  ThreadRecord,
  TranscriptItem,
  TurnRecord,
} from "../types/loom";
import { PRESENTATION_FRAME_MS } from "../presentationTiming";
import { isLoomWebRuntime } from "../webBridge";
import { reconcilePendingUserMessage } from "../pendingUserMessage";
import { buildApprovalResponse } from "./approvalProtocol";

type ThreadView = "active" | "archived";
type ThreadCounts = { active: number; archived: number; all: number };
type ThreadListResult = { threads: ThreadRecord[]; counts?: Partial<ThreadCounts> };
type ThreadReadCacheEntry = { result: ThreadReadResult; cachedAt: number };

const THREAD_READ_CACHE_LIMIT = 8;
const THREAD_READ_CACHE_TTL_MS = 5 * 60_000;
const THREAD_READ_WINDOW_TURNS = 20;
const MODEL_CATALOG_POLL_MS = 60_000;

function flattenItems(turns: TurnRecord[]): TranscriptItem[] {
  return turns.flatMap((turn) => turn.items ?? []);
}

/**
 * New Hosts return a bounded turn window directly. Older Hosts ignore the
 * pagination parameters and return the whole thread; trim that response on the
 * client so long transcripts still avoid building thousands of React nodes.
 */
function normalizeThreadReadWindow(result: ThreadReadResult, beforeTurnId = ""): ThreadReadResult {
  const turns = result.turns ?? [];
  if (typeof result.hasMoreTurns === "boolean") {
    return {
      ...result,
      oldestTurnId: result.oldestTurnId ?? turns[0]?.id ?? null,
    };
  }

  let end = turns.length;
  if (beforeTurnId) {
    const cursorIndex = turns.findIndex((turn) => turn.id === beforeTurnId);
    if (cursorIndex < 0) {
      return { ...result, turns: [], hasMoreTurns: false, oldestTurnId: null };
    }
    end = cursorIndex;
  }
  const start = Math.max(0, end - THREAD_READ_WINDOW_TURNS);
  const windowTurns = turns.slice(start, end);
  return {
    ...result,
    turns: windowTurns,
    hasMoreTurns: start > 0,
    oldestTurnId: windowTurns[0]?.id ?? null,
  };
}

function buildItemIndex(items: TranscriptItem[]): Map<string, number> {
  const index = new Map<string, number>();
  for (let position = 0; position < items.length; position += 1) {
    index.set(items[position].id, position);
  }
  return index;
}

function indexedItemPosition(index: Map<string, number>, items: TranscriptItem[], itemId: string): number {
  const cached = index.get(itemId);
  if (cached !== undefined && items[cached]?.id === itemId) return cached;
  const repaired = items.findIndex((item) => item.id === itemId);
  if (repaired >= 0) index.set(itemId, repaired);
  else index.delete(itemId);
  return repaired;
}

function mergeDelta(item: TranscriptItem, delta: Record<string, unknown>): TranscriptItem {
  const next: TranscriptItem = { ...item };
  for (const [key, value] of Object.entries(delta)) {
    if (["text", "reasoning", "stdout", "stderr"].includes(key) && typeof value === "string") {
      next[key] = `${String(next[key] ?? "")}${value}`;
    } else if (key === "arguments" && delta.kind === "tool_call_argument" && typeof value === "string") {
      next.arguments = `${typeof next.arguments === "string" ? next.arguments : ""}${value}`;
    } else {
      next[key] = value;
    }
  }
  return next;
}

function mergeQueuedDelta(
  previous: Record<string, unknown> | undefined,
  incoming: Record<string, unknown>,
): Record<string, unknown> {
  const next = { ...(previous ?? {}) };
  for (const [key, value] of Object.entries(incoming)) {
    if (["text", "reasoning", "stdout", "stderr"].includes(key) && typeof value === "string") {
      next[key] = `${String(next[key] ?? "")}${value}`;
    } else if (key === "arguments" && incoming.kind === "tool_call_argument" && typeof value === "string") {
      next.kind = incoming.kind;
      next.arguments = `${typeof next.arguments === "string" ? next.arguments : ""}${value}`;
    } else {
      next[key] = value;
    }
  }
  return next;
}

function getBridge(): Window["loom"] | null {
  return Reflect.get(window, "loom") as Window["loom"] | null;
}

function requireBridge(): Window["loom"] {
  const bridge = getBridge();
  if (!bridge) {
    throw new Error("Loom preload bridge is unavailable. Check the Electron preload build.");
  }
  return bridge;
}

function threadIsRunning(thread?: ThreadRecord | null): boolean {
  return thread?.status === "running" || thread?.status === "waiting_approval";
}

function itemIsTerminalTurnError(item: TranscriptItem): boolean {
  if (item.type !== "error") return false;
  const status = String(item.status ?? "").toLowerCase();
  return !["started", "running", "streaming", "pending", "waiting"].includes(status);
}

function terminalErrorTurnId(items: TranscriptItem[], preferredTurnId?: string | null): string {
  const preferred = String(preferredTurnId ?? "").trim();
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (!itemIsTerminalTurnError(item)) continue;
    const turnId = String(item.turnId ?? "").trim();
    if (!preferred || !turnId || turnId === preferred) return turnId || preferred;
  }
  return "";
}

function modelsForThread(snapshot: ModelSnapshot | null, thread?: ThreadRecord | null): ModelSnapshot | null {
  if (!snapshot || !thread?.modelSelection) return snapshot;
  const profile = snapshot.profiles.find((candidate) => candidate.selection === thread.modelSelection);

  if (!profile) {
    // Catalog refreshes are allowed to remove models, but a conversation is an
    // immutable record of the model it was bound to. Preserve that binding in
    // the UI as an unavailable row instead of falling back visually (or
    // operationally) to whatever model happens to be globally current.
    const model = String(thread.model || "").trim() || "Unavailable model";
    const adapter = String(thread.modelProvider || "").trim() || "openai-compatible";
    const unavailable = {
      selection: thread.modelSelection,
      id: `unavailable:${thread.modelSelection}`,
      kind: thread.modelSelection.startsWith("profile:") ? "saved" as const : "builtin" as const,
      name: model,
      adapter,
      baseUrl: String(thread.modelBaseUrl || ""),
      model,
      available: false,
      catalogSource: "runtime",
      statusMessage: "This model is no longer advertised by the provider. Choose another model to replace this conversation binding.",
      vision: thread.modelVision ?? true,
      reasoning: null,
    };
    return {
      ...snapshot,
      profiles: [...snapshot.profiles, unavailable],
      current: {
        ...unavailable,
        provider: adapter,
        reasoning: thread.reasoning
          ? { kind: thread.reasoning.kind, value: thread.reasoning.value, defaultValue: thread.reasoning.value, options: [], source: "thread" }
          : null,
      },
    };
  }

  const reasoning = profile.reasoning && thread.reasoning
    ? { ...profile.reasoning, ...thread.reasoning }
    : profile.reasoning ?? null;
  return {
    ...snapshot,
    current: {
      ...profile,
      model: thread.model || profile.model,
      provider: thread.modelProvider || profile.adapter,
      baseUrl: thread.modelBaseUrl || profile.baseUrl,
      // `profile` is the catalogue entry resolved just now; `modelVision` is a
      // snapshot taken whenever this thread last switched models. For a
      // capability the live answer wins -- reading the snapshot first left
      // threads permanently refusing images after the catalogue was corrected,
      // with no way back: the block applies before a turn can start, and a turn
      // is what would have refreshed the record. A saved model's user-declared
      // `vision: false` reaches us through `profile`, so it still holds.
      vision: profile.vision ?? thread.modelVision ?? true,
      reasoning,
    },
  };
}

function turnStartFromRead(result: ThreadReadResult): number | null {
  if (!threadIsRunning(result.thread)) return null;
  const currentTurn = result.thread.currentTurnId
    ? (result.turns ?? []).find((turn) => turn.id === result.thread.currentTurnId)
    : [...(result.turns ?? [])].reverse().find((turn) => turn.status === "running" || turn.status === "waiting_approval");
  const stamp = Date.parse(String(currentTurn?.startedAt ?? ""));
  return Number.isFinite(stamp) ? stamp : Date.now();
}

export function useLoom() {
  const [connection, setConnection] = useState<"connecting" | "ready" | "error">("connecting");
  const [error, setError] = useState("");
  const [runtime, setRuntime] = useState<InitializeResult["runtime"]>({});
  const [models, setModels] = useState<ModelSnapshot | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [projectsSupported, setProjectsSupported] = useState(false);
  const [threadView, setThreadViewState] = useState<ThreadView>("active");
  const [threadCounts, setThreadCounts] = useState<ThreadCounts>({ active: 0, archived: 0, all: 0 });
  const [active, setActive] = useState<ThreadReadResult | null>(null);
  const [openingThreadId, setOpeningThreadId] = useState("");
  const [loadingOlderTurns, setLoadingOlderTurns] = useState(false);
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [turnActive, setTurnActive] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const [context, setContext] = useState<ContextReport | null>(null);
  const [compactionProgress, setCompactionProgress] = useState<ContextCompactionProgress | null>(null);
  const compacting = compactionProgress?.status === "started" || compactionProgress?.status === "running";
  const activeIdRef = useRef("");
  const activeTurnIdRef = useRef("");
  const openRequestRef = useRef(0);
  const openingThreadIdRef = useRef("");
  const loadingOlderTurnsRef = useRef(false);
  const threadReadCacheRef = useRef<Map<string, ThreadReadCacheEntry>>(new Map());
  const threadReadInflightRef = useRef<Map<string, Promise<ThreadReadResult>>>(new Map());
  const threadsRef = useRef<ThreadRecord[]>([]);
  const threadViewRef = useRef<ThreadView>("active");
  const terminalErrorTurnRef = useRef("");
  const itemIndexRef = useRef<Map<string, number>>(new Map());
  const pendingItemDeltasRef = useRef<Map<string, Record<string, unknown>>>(new Map());
  const deltaFlushTimerRef = useRef<number | null>(null);

  const flushPendingItemDeltas = useCallback(() => {
    if (deltaFlushTimerRef.current !== null) {
      window.clearTimeout(deltaFlushTimerRef.current);
      deltaFlushTimerRef.current = null;
    }
    if (!pendingItemDeltasRef.current.size) return;

    const pending = pendingItemDeltasRef.current;
    pendingItemDeltasRef.current = new Map();
    setItems((current) => {
      let next: TranscriptItem[] | null = null;
      for (const [itemId, delta] of pending) {
        const index = indexedItemPosition(itemIndexRef.current, next ?? current, itemId);
        if (index < 0) continue;
        next ??= [...current];
        next[index] = mergeDelta(next[index], delta);
      }
      return next ?? current;
    });
  }, []);

  const scheduleItemDeltaFlush = useCallback(() => {
    if (deltaFlushTimerRef.current !== null) return;
    deltaFlushTimerRef.current = window.setTimeout(() => {
      deltaFlushTimerRef.current = null;
      flushPendingItemDeltas();
    }, PRESENTATION_FRAME_MS);
  }, [flushPendingItemDeltas]);

  useEffect(() => () => {
    if (deltaFlushTimerRef.current !== null) window.clearTimeout(deltaFlushTimerRef.current);
    deltaFlushTimerRef.current = null;
    pendingItemDeltasRef.current.clear();
  }, []);

  useEffect(() => {
    activeIdRef.current = active?.thread.id ?? "";
    activeTurnIdRef.current = String(active?.thread.currentTurnId ?? "");
  }, [active?.thread.currentTurnId, active?.thread.id]);

  useEffect(() => {
    threadsRef.current = threads;
  }, [threads]);

  const installItems = useCallback((next: TranscriptItem[]) => {
    if (deltaFlushTimerRef.current !== null) window.clearTimeout(deltaFlushTimerRef.current);
    deltaFlushTimerRef.current = null;
    pendingItemDeltasRef.current.clear();
    itemIndexRef.current = buildItemIndex(next);
    setItems(next);
  }, []);

  const clearActive = useCallback(() => {
    activeIdRef.current = "";
    activeTurnIdRef.current = "";
    terminalErrorTurnRef.current = "";
    openingThreadIdRef.current = "";
    loadingOlderTurnsRef.current = false;
    setOpeningThreadId("");
    setLoadingOlderTurns(false);
    setActive(null);
    installItems([]);
    setTurnActive(false);
    setTurnStartedAt(null);
    setContext(null);
    setCompactionProgress(null);
  }, [installItems]);

  const refreshContext = useCallback(async (threadId: string) => {
    if (!threadId) return null;
    try {
      const result = await requireBridge().call<{ context: ContextReport }>("thread/context", { threadId });
      // A slow report for a thread the user already left must not overwrite the
      // one they are looking at now.
      if (activeIdRef.current !== threadId) return null;
      setContext(result.context ?? null);
      return result.context ?? null;
    } catch {
      // An older app server without the context capability simply has no meter.
      return null;
    }
  }, []);

  const compactContext = useCallback(async (keepRecent?: number) => {
    const threadId = activeIdRef.current;
    if (!threadId) return;
    const optimistic: ContextCompactionProgress = {
      threadId,
      operationId: "pending",
      status: "started",
      stage: "queued",
      message: "Context compaction queued",
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    setCompactionProgress(optimistic);
    try {
      const result = await requireBridge().call<{
        operationId: string;
        startedAt: string;
      }>("thread/compact", {
        threadId,
        ...(keepRecent ? { keepRecent } : {}),
      });
      setCompactionProgress((current) => current && current.threadId === threadId
        ? { ...current, operationId: result.operationId, startedAt: result.startedAt || current.startedAt }
        : current);
    } catch (error) {
      setCompactionProgress(null);
      throw error;
    }
  }, []);

  const refreshThreads = useCallback(async (viewOverride?: ThreadView) => {
    const view = viewOverride ?? threadViewRef.current;
    const result = await requireBridge().call<ThreadListResult>("thread/list", { view, limit: 100 });
    const next = result.threads ?? [];
    setThreads(next);
    if (result.counts) {
      setThreadCounts((current) => ({
        active: Number(result.counts?.active ?? current.active),
        archived: Number(result.counts?.archived ?? current.archived),
        all: Number(result.counts?.all ?? current.all),
      }));
    }
    return next;
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      const result = await requireBridge().call<ProjectListResult>("project/list", {});
      setProjects(result.projects ?? []);
      setProjectsSupported(true);
      return result.projects ?? [];
    } catch {
      setProjectsSupported(false);
      setProjects([]);
      return [];
    }
  }, []);

  const createProject = useCallback(async (root: string, name?: string) => {
    const params: Record<string, unknown> = { root };
    if (name?.trim()) params.name = name.trim();
    const result = await requireBridge().call<{ project: ProjectRecord }>("project/create", params);
    await refreshProjects();
    return result.project;
  }, [refreshProjects]);

  const renameProject = useCallback(async (projectId: string, name: string) => {
    await requireBridge().call("project/rename", { projectId, name });
    await refreshProjects();
  }, [refreshProjects]);

  const setProjectInstructions = useCallback(async (projectId: string, instructions: string) => {
    const result = await requireBridge().call<{ project: ProjectRecord }>("project/set_instructions", {
      projectId,
      instructions,
    });
    const updated = result.project;
    setProjects((current) => current.map((project) => (project.id === updated.id ? updated : project)));
    return updated;
  }, []);

  const removeProject = useCallback(async (projectId: string) => {
    await requireBridge().call("project/remove", { projectId });
    await refreshProjects();
    await refreshThreads();
  }, [refreshProjects, refreshThreads]);

  const refreshModels = useCallback(async (forceRefresh = false) => {
    const snapshot = await requireBridge().listModels<ModelSnapshot>(forceRefresh);
    setModels(snapshot);
    return snapshot;
  }, []);

  const rememberThreadRead = useCallback((result: ThreadReadResult) => {
    const threadId = result.thread.id;
    const cache = threadReadCacheRef.current;
    cache.delete(threadId);
    if (threadIsRunning(result.thread)) return;
    cache.set(threadId, { result, cachedAt: Date.now() });
    while (cache.size > THREAD_READ_CACHE_LIMIT) {
      const oldest = cache.keys().next().value as string | undefined;
      if (!oldest) break;
      cache.delete(oldest);
    }
  }, []);

  const cachedThreadRead = useCallback((threadId: string): ThreadReadResult | null => {
    const entry = threadReadCacheRef.current.get(threadId);
    if (!entry) return null;
    if (Date.now() - entry.cachedAt > THREAD_READ_CACHE_TTL_MS) {
      threadReadCacheRef.current.delete(threadId);
      return null;
    }
    const listed = threadsRef.current.find((thread) => thread.id === threadId);
    if (
      listed
      && listed.updatedAt
      && entry.result.thread.updatedAt
      && listed.updatedAt !== entry.result.thread.updatedAt
    ) {
      threadReadCacheRef.current.delete(threadId);
      return null;
    }
    threadReadCacheRef.current.delete(threadId);
    threadReadCacheRef.current.set(threadId, entry);
    return entry.result;
  }, []);

  const readThread = useCallback((threadId: string, beforeTurnId = ""): Promise<ThreadReadResult> => {
    const requestKey = `${threadId}:${beforeTurnId || "latest"}`;
    const existing = threadReadInflightRef.current.get(requestKey);
    if (existing) return existing;

    const request = requireBridge()
      .call<ThreadReadResult>("thread/read", {
        threadId,
        presentationOnly: true,
        turnLimit: THREAD_READ_WINDOW_TURNS,
        ...(beforeTurnId ? { beforeTurnId } : {}),
      })
      .then((result) => normalizeThreadReadWindow(result, beforeTurnId))
      .finally(() => {
        if (threadReadInflightRef.current.get(requestKey) === request) {
          threadReadInflightRef.current.delete(requestKey);
        }
      });
    threadReadInflightRef.current.set(requestKey, request);
    return request;
  }, []);

  const prefetchThread = useCallback((threadId: string) => {
    const normalized = threadId.trim();
    if (!normalized || normalized === activeIdRef.current) return;
    const listed = threadsRef.current.find((thread) => thread.id === normalized);
    if (!listed || threadIsRunning(listed)) return;
    if (cachedThreadRead(normalized)) return;

    void readThread(normalized)
      .then((result) => rememberThreadRead(result))
      .catch(() => {
        // Hover/focus prefetch is opportunistic. Navigation itself will surface
        // a real read failure if the user actually opens this conversation.
      });
  }, [cachedThreadRead, readThread, rememberThreadRead]);

  const applyThreadRead = useCallback((result: ThreadReadResult) => {
    activeIdRef.current = result.thread.id;
    loadingOlderTurnsRef.current = false;
    setLoadingOlderTurns(false);
    activeTurnIdRef.current = String(result.thread.currentTurnId ?? "");
    const nextItems = flattenItems(result.turns ?? []);
    const terminalTurnId = terminalErrorTurnId(nextItems, result.thread.currentTurnId);
    terminalErrorTurnRef.current = terminalTurnId;
    const normalizedResult = terminalTurnId && threadIsRunning(result.thread)
      ? {
          ...result,
          thread: {
            ...result.thread,
            status: "failed",
          },
        }
      : result;

    setActive(normalizedResult);
    installItems(nextItems);
    const running = threadIsRunning(normalizedResult.thread);
    setTurnActive(running);
    setTurnStartedAt(running ? turnStartFromRead(normalizedResult) : null);
    setContext(null);
    setCompactionProgress(null);
    rememberThreadRead(normalizedResult);
    void refreshContext(normalizedResult.thread.id);
  }, [installItems, refreshContext, rememberThreadRead]);

  const openThread = useCallback(async (threadId: string) => {
    const normalized = threadId.trim();
    if (!normalized) return;

    if (normalized === activeIdRef.current) {
      if (openingThreadIdRef.current && openingThreadIdRef.current !== normalized) {
        openRequestRef.current += 1;
        openingThreadIdRef.current = "";
        setOpeningThreadId("");
      }
      return;
    }

    const requestId = ++openRequestRef.current;
    openingThreadIdRef.current = normalized;
    setOpeningThreadId(normalized);

    const cached = cachedThreadRead(normalized);
    if (cached) {
      applyThreadRead(cached);
      if (openRequestRef.current === requestId) {
        openingThreadIdRef.current = "";
        setOpeningThreadId("");
      }
      return;
    }

    try {
      const result = await readThread(normalized);
      if (openRequestRef.current !== requestId) return;
      applyThreadRead(result);
    } finally {
      if (openRequestRef.current === requestId) {
        openingThreadIdRef.current = "";
        setOpeningThreadId("");
      }
    }
  }, [applyThreadRead, cachedThreadRead, readThread]);

  const loadOlderTurns = useCallback(async () => {
    const snapshot = active;
    if (
      !snapshot?.thread.id
      || !snapshot.hasMoreTurns
      || loadingOlderTurnsRef.current
    ) return;

    const threadId = snapshot.thread.id;
    const oldestTurnId = snapshot.turns?.[0]?.id || snapshot.oldestTurnId || "";
    if (!oldestTurnId) return;

    loadingOlderTurnsRef.current = true;
    setLoadingOlderTurns(true);
    try {
      const page = await readThread(threadId, oldestTurnId);
      if (activeIdRef.current !== threadId) return;

      const olderTurns = page.turns ?? [];
      if (!olderTurns.length) {
        setActive((current) => current && current.thread.id === threadId
          ? { ...current, hasMoreTurns: false }
          : current);
        return;
      }

      setActive((current) => {
        if (!current || current.thread.id !== threadId) return current;
        const existingTurnIds = new Set((current.turns ?? []).map((turn) => turn.id));
        const prependedTurns = olderTurns.filter((turn) => !existingTurnIds.has(turn.id));
        return {
          ...current,
          turns: [...prependedTurns, ...(current.turns ?? [])],
          hasMoreTurns: Boolean(page.hasMoreTurns),
          oldestTurnId: prependedTurns[0]?.id ?? current.oldestTurnId ?? null,
        };
      });

      const olderItems = flattenItems(olderTurns);
      setItems((current) => {
        const existingItemIds = new Set(current.map((item) => item.id));
        const prependedItems = olderItems.filter((item) => !existingItemIds.has(item.id));
        if (!prependedItems.length) return current;
        const next = [...prependedItems, ...current];
        itemIndexRef.current = buildItemIndex(next);
        return next;
      });
    } finally {
      loadingOlderTurnsRef.current = false;
      setLoadingOlderTurns(false);
    }
  }, [active, readThread]);

  const ensureSelection = useCallback(async (list: ThreadRecord[], preferredId = activeIdRef.current) => {
    if (preferredId && list.some((thread) => thread.id === preferredId)) return;
    if (list.length) {
      await openThread(list[0].id);
    } else {
      clearActive();
    }
  }, [clearActive, openThread]);

  const setThreadView = useCallback(async (view: ThreadView) => {
    threadViewRef.current = view;
    setThreadViewState(view);
    const list = await refreshThreads(view);
    const currentId = activeIdRef.current;
    if (currentId && list.some((thread) => thread.id === currentId)) return;

    // A list-view switch is navigation, not a request to open whichever row
    // happens to sort first. Invalidate any in-flight read from the previous
    // view, then leave the destination list unselected until the user clicks.
    openRequestRef.current += 1;
    clearActive();
  }, [clearActive, refreshThreads]);

  const newThread = useCallback(async (workspace?: string, projectId?: string) => {
    const requestId = ++openRequestRef.current;
    const params: Record<string, unknown> = projectId?.trim()
      ? { projectId: projectId.trim() }
      : workspace?.trim()
        ? { workspace: workspace.trim() }
        : {};
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/start", params);
    threadViewRef.current = "active";
    setThreadViewState("active");
    setThreads((current) => {
      const exists = current.some((thread) => thread.id === result.thread.id);
      return exists
        ? current.map((thread) => thread.id === result.thread.id ? result.thread : thread)
        : [result.thread, ...current];
    });
    setThreadCounts((current) => ({
      active: current.active + 1,
      archived: current.archived,
      all: current.all + 1,
    }));

    // thread/start already returns the complete record for a brand-new, empty
    // conversation. Render it immediately instead of serially rescanning the
    // catalogue and reading the same empty thread back from disk.
    if (openRequestRef.current === requestId) {
      activeIdRef.current = result.thread.id;
      activeTurnIdRef.current = "";
      terminalErrorTurnRef.current = "";
      openingThreadIdRef.current = "";
      setOpeningThreadId("");
      setActive({ thread: result.thread, turns: [], hasMoreTurns: false, oldestTurnId: null });
      installItems([]);
      setTurnActive(false);
      setTurnStartedAt(null);
      setContext(null);
      setCompactionProgress(null);
    }
    void refreshThreads("active");
  }, [installItems, refreshThreads]);

  const renameThread = useCallback(async (threadId: string, title: string) => {
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/rename", { threadId, title });
    const updated = result.thread;
    setThreads((current) => current.map((thread) => (thread.id === updated.id ? updated : thread)));
    setActive((current) => current && current.thread.id === updated.id ? { ...current, thread: updated } : current);
  }, []);

  const archiveThread = useCallback(async (threadId: string, archived: boolean) => {
    await requireBridge().call<{ thread: ThreadRecord }>("thread/archive", { threadId, archived });
    const list = await refreshThreads();
    await ensureSelection(list);
  }, [ensureSelection, refreshThreads]);

  const deleteThread = useCallback(async (threadId: string) => {
    await requireBridge().call("thread/delete", { threadId });
    const list = await refreshThreads();
    await ensureSelection(list, activeIdRef.current === threadId ? "" : activeIdRef.current);
  }, [ensureSelection, refreshThreads]);

  const forkThread = useCallback(async (threadId: string) => {
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/fork", { threadId });
    threadViewRef.current = "active";
    setThreadViewState("active");
    await refreshThreads("active");
    await openThread(result.thread.id);
  }, [openThread, refreshThreads]);

  const send = useCallback(async (input: string, attachments: { path: string; name: string }[] = []) => {
    if (!active?.thread.id || active.thread.archived) return;
    if (!input.trim() && !attachments.length) return;
    threadReadCacheRef.current.delete(active.thread.id);
    const pendingId = `pending-user-${crypto.randomUUID()}`;
    if (input.trim()) setItems((current) => [...current, {
      id: pendingId, threadId: active.thread.id, type: "user_message",
      text: input.trim(), status: "sending", submittedAt: new Date().toISOString(),
    }]);
    setTurnActive(true);
    setTurnStartedAt(Date.now());
    try {
      const params: Record<string, unknown> = { threadId: active.thread.id, input: input.trim() };
      if (attachments.length) params.attachments = attachments;
      const result = await requireBridge().call<{ turn: TurnRecord }>("turn/start", params);
      const turn = result.turn;
      setActive((current) => current && current.thread.id === active.thread.id
        ? {
            ...current,
            pendingApproval: null,
            thread: {
              ...current.thread,
              currentTurnId: turn.id,
            },
          }
        : current);
    } catch (cause) {
      setItems((current) => current.filter((item) => item.id !== pendingId));
      setTurnActive(false);
      setTurnStartedAt(null);
      throw cause;
    }
  }, [active?.thread.archived, active?.thread.id]);

  const interrupt = useCallback(async () => {
    if (!active?.thread.id || !active.thread.currentTurnId) return;
    await requireBridge().call("turn/interrupt", {
      threadId: active.thread.id,
      turnId: active.thread.currentTurnId,
    });
  }, [active?.thread.currentTurnId, active?.thread.id]);

  const setPermissionMode = useCallback(async (permissionMode: string) => {
    if (!active?.thread.id || active.thread.archived) return;
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/set_permission_mode", {
      threadId: active.thread.id,
      permissionMode,
    });
    const updated = result.thread;
    setThreads((current) => current.map((thread) => (thread.id === updated.id ? updated : thread)));
    setActive((current) => current && current.thread.id === updated.id ? { ...current, thread: updated } : current);
  }, [active?.thread.archived, active?.thread.id]);

  const applyModelRestart = useCallback(async (result: ModelRestartResult) => {
    setRuntime((current) => ({ ...current, ...(result.initialization.runtime ?? {}) }));
    setModels(result.models);
    if (result.thread) {
      const updated = result.thread;
      setThreads((current) => current.map((thread) => (thread.id === updated.id ? updated : thread)));
      setActive((current) => current && current.thread.id === updated.id ? { ...current, thread: updated } : current);
    }
    if (result.hotSwitch) return;
    const preferredId = activeIdRef.current;
    const list = await refreshThreads();
    if (preferredId && list.some((thread) => thread.id === preferredId)) {
      await openThread(preferredId);
    } else if (list.length) {
      await openThread(list[0].id);
    } else {
      clearActive();
    }
  }, [clearActive, openThread, refreshThreads]);

  const switchModelProfile = useCallback(async (selection: string) => {
    if (!active?.thread.id) return;
    setModelBusy(true);
    try {
      const result = await requireBridge().switchModelProfile<ModelRestartResult>(active.thread.id, selection);
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [active?.thread.id, applyModelRestart]);

  const switchCurrentModel = useCallback(async (model: string) => {
    if (!active?.thread.id) return;
    const selection = active.thread.modelSelection || models?.current?.selection || "";
    if (!selection) return;
    setModelBusy(true);
    try {
      const result = await requireBridge().switchCurrentModel<ModelRestartResult>(
        active.thread.id,
        selection,
        model,
      );
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [active?.thread.id, active?.thread.modelSelection, applyModelRestart, models?.current?.selection]);

  const configureModelProvider = useCallback(async (provider: string, apiKey: string) => {
    setModelBusy(true);
    try {
      const snapshot = await requireBridge().setModelProviderKey<ModelSnapshot>(provider, apiKey);
      setModels(snapshot);
    } finally {
      setModelBusy(false);
    }
  }, []);

  const addModel = useCallback(async (input: AddModelInput) => {
    setModelBusy(true);
    try {
      const result = active?.thread.id
        ? await requireBridge().addModel<ModelRestartResult>(active.thread.id, { ...input })
        : await requireBridge().addModel<ModelRestartResult>({ ...input });
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [active?.thread.id, applyModelRestart]);

  const deleteModel = useCallback(async (selection: string) => {
    setModelBusy(true);
    try {
      const result = await requireBridge().deleteModel<ModelRestartResult>(selection);
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [applyModelRestart]);

  const setReasoning = useCallback(async (kind: string, value: string) => {
    if (!active?.thread.id) return;
    const selection = active.thread.modelSelection || models?.current?.selection || "";
    const model = active.thread.model || models?.current?.model || "";
    if (!selection || !model) return;
    setModelBusy(true);
    try {
      const result = await requireBridge().setReasoning<ReasoningUpdateResult>(
        active.thread.id,
        selection,
        model,
        kind,
        value,
      );
      setRuntime((current) => ({ ...current, ...(result.runtime ?? {}) }));
      setModels(result.models);
      if (result.thread) {
        const updated = result.thread;
        setThreads((current) => current.map((thread) => (thread.id === updated.id ? updated : thread)));
        setActive((current) => current && current.thread.id === updated.id ? { ...current, thread: updated } : current);
      }
    } finally {
      setModelBusy(false);
    }
  }, [active?.thread.id, active?.thread.model, active?.thread.modelSelection, models?.current?.model, models?.current?.selection]);

  const respondApproval = useCallback(async (item: TranscriptItem, approved: boolean) => {
    if (!active?.thread.id || !item.callId) return;
    const params = buildApprovalResponse(active.pendingApproval, item.callId, approved);
    if (params.threadId !== active.thread.id) {
      throw new Error("The pending approval belongs to a different thread. Reload the thread.");
    }
    await requireBridge().call("approval/respond", params);
  }, [active?.pendingApproval, active?.thread.id]);

  useEffect(() => {
    const bridge = getBridge();
    if (!bridge) {
      setError("Loom preload bridge is unavailable. The renderer started, but Electron did not expose window.loom.");
      setConnection("error");
      return;
    }

    const unsubscribe = bridge.onNotification((message) => {
      const params = message.params ?? {};
      if (message.method === "host/disconnected") {
        if (isLoomWebRuntime()) return; // WebAppGate reconnects without discarding the workspace.
        setConnection("error");
        setError("Loom Host stopped. Reopen Loom to reconnect.");
        return;
      }
      if (message.method === "runtime/updated") {
        const nextRuntime = params.runtime as InitializeResult["runtime"] | undefined;
        if (nextRuntime) setRuntime((current) => ({ ...current, ...nextRuntime }));
        return;
      }

      const nestedItem = params.item as Record<string, unknown> | undefined;
      const threadId = String(params.threadId ?? nestedItem?.threadId ?? "");
      const activeId = activeIdRef.current;

      if (message.method === "thread/updated") {
        const incomingThread = params.thread as ThreadRecord | undefined;
        if (incomingThread) {
          const staleRunningAfterTerminalError = Boolean(
            terminalErrorTurnRef.current
            && String(incomingThread.currentTurnId ?? "") === terminalErrorTurnRef.current
            && threadIsRunning(incomingThread)
          );
          const thread = staleRunningAfterTerminalError
            ? { ...incomingThread, status: "failed" }
            : incomingThread;

          threadReadCacheRef.current.delete(thread.id);
          const belongsInView = threadViewRef.current === "archived" ? Boolean(thread.archived) : !thread.archived;
          setThreads((current) => {
            const exists = current.some((entry) => entry.id === thread.id);
            if (!belongsInView) return current.filter((entry) => entry.id !== thread.id);
            if (exists) return current.map((entry) => (entry.id === thread.id ? thread : entry));
            return [thread, ...current];
          });
          if (thread.id === activeId) {
            setActive((current) => current && current.thread.id === thread.id ? { ...current, thread } : current);
            const running = threadIsRunning(thread);
            setTurnActive(running);
            if (running) setTurnStartedAt((current) => current ?? Date.now());
            else setTurnStartedAt(null);
          }
        }
        return;
      }
      if (message.method === "thread/deleted") {
        const deletedId = String(params.threadId ?? "");
        threadReadCacheRef.current.delete(deletedId);
        setThreads((current) => current.filter((entry) => entry.id !== deletedId));
        if (deletedId && deletedId === activeId) clearActive();
        return;
      }
      if (message.method === "turn/started") {
        const turn = params.turn as TurnRecord | undefined;
        if (turn && threadId === activeId) {
          activeTurnIdRef.current = turn.id;
          terminalErrorTurnRef.current = "";
          threadReadCacheRef.current.delete(threadId);
          setActive((current) => current && current.thread.id === activeId
            ? {
                ...current,
                pendingApproval: null,
                thread: {
                  ...current.thread,
                  status: "running",
                  currentTurnId: turn.id,
                },
              }
            : current);
          setTurnActive(true);
          const stamp = Date.parse(String(turn.startedAt ?? ""));
          setTurnStartedAt(Number.isFinite(stamp) ? stamp : Date.now());
        }
        return;
      }
      if (message.method === "thread/started" && threadViewRef.current === "active") void refreshThreads("active");
      if (!activeId || threadId !== activeId) return;

      if (message.method === "context/updated") {
        const report = params.context as ContextReport | undefined;
        if (report) {
          setContext(report);
        }
        return;
      }

      if (message.method === "context/compaction") {
        const progress = params as unknown as ContextCompactionProgress;
        if (progress.threadId === activeId) setCompactionProgress(progress);
        return;
      }

      if (message.method === "item/started") {
        const item = params.item as TranscriptItem | undefined;
        if (item) {
          setTurnActive(true);
          setTurnStartedAt((current) => current ?? Date.now());
          setItems((current) => {
            const existing = indexedItemPosition(itemIndexRef.current, current, item.id);
            if (existing >= 0) return current;
            itemIndexRef.current.set(item.id, current.length);
            return [...current, item];
          });
        }
      } else if (message.method === "item/delta") {
        const itemId = String(params.itemId ?? "");
        const delta = (params.delta ?? {}) as Record<string, unknown>;
        if (itemId) {
          pendingItemDeltasRef.current.set(
            itemId,
            mergeQueuedDelta(pendingItemDeltasRef.current.get(itemId), delta),
          );
          scheduleItemDeltaFlush();
        }
      } else if (message.method === "item/completed") {
        const completed = params.item as TranscriptItem | undefined;
        if (completed) {
          const queuedDelta = pendingItemDeltasRef.current.get(completed.id);
          pendingItemDeltasRef.current.delete(completed.id);
          setItems((current) => {
            current = reconcilePendingUserMessage(current, completed);
            const index = indexedItemPosition(itemIndexRef.current, current, completed.id);
            if (index < 0) {
              itemIndexRef.current.set(completed.id, current.length);
              return [...current, completed];
            }
            const next = [...current];
            const withQueuedDelta = queuedDelta ? mergeDelta(current[index], queuedDelta) : current[index];
            next[index] = { ...withQueuedDelta, ...completed };
            return next;
          });
          if (completed.type === "approval" && completed.callId) {
            setActive((current) => current && current.pendingApproval?.callId === completed.callId
              ? { ...current, pendingApproval: null }
              : current);
          }

          if (itemIsTerminalTurnError(completed)) {
            setItems((current) => current.map((item) => item.threadId === completed.threadId && item.status === "sending"
              ? { ...item, status: "failed" } : item));
            // Some provider/transport failures terminate the worker by emitting
            // a durable ERROR item before (or, on broken upstreams, without)
            // TURN_COMPLETED. Treat that item as an authoritative terminal
            // boundary so the composer returns to normal send mode immediately.
            // Otherwise the next message is misrouted through turn/steer into a
            // dead turn and the red error remains the last visible state.
            const failedTurnId = String(completed.turnId ?? activeTurnIdRef.current ?? "").trim();
            terminalErrorTurnRef.current = failedTurnId || terminalErrorTurnRef.current;
            threadReadCacheRef.current.delete(activeId);
            setTurnActive(false);
            setTurnStartedAt(null);
            setActive((current) => {
              if (!current || current.thread.id !== activeId) return current;
              if (
                failedTurnId
                && current.thread.currentTurnId
                && current.thread.currentTurnId !== failedTurnId
              ) return current;
              return {
                ...current,
                pendingApproval: null,
                turns: (current.turns ?? []).map((entry) => (
                  !failedTurnId || entry.id === failedTurnId
                    ? { ...entry, status: "failed" }
                    : entry
                )),
                thread: {
                  ...current.thread,
                  status: "failed",
                },
              };
            });
          }
        }
      } else if (message.method === "thread/resync") {
        void openThread(activeId);
      } else if (message.method === "approval/requested") {
        const approval = params.approval as PendingApproval | undefined;
        if (approval && approval.threadId === activeId) {
          setActive((current) => current && current.thread.id === activeId
            ? {
                ...current,
                pendingApproval: approval,
                thread: { ...current.thread, status: "waiting_approval" },
              }
            : current);
        }
      } else if (message.method === "turn/completed") {
        const turn = params.turn as TurnRecord | undefined;
        // A delayed completion from a previous turn must not stop a new turn
        // (or switch its composer from steer back to send).
        if (turn && activeTurnIdRef.current && turn.id !== activeTurnIdRef.current) return;
        // Keep a terminal-error guard for this turn until TURN_STARTED names a
        // genuinely new turn. Late/stale thread updates from the failed worker
        // must not resurrect steering mode after completion.
        flushPendingItemDeltas();
        setTurnActive(false);
        setTurnStartedAt(null);
        if (turn) {
          const finalItemId = String(turn.finalItemId ?? "");
          const finalStepId = String(turn.finalStepId ?? "");
          if (turn.status === "completed" && (finalItemId || finalStepId)) {
            setItems((current) => current.map((item) => {
              if (item.turnId !== turn.id || item.type !== "assistant_message") return item;
              const isFinal = Boolean(
                (finalItemId && item.id === finalItemId)
                || (finalStepId && String(item.stepId ?? "") === finalStepId),
              );
              const phase = isFinal ? "final_answer" : (item.phase || "commentary");
              return item.phase === phase ? item : { ...item, phase };
            }));
          }
          setActive((current) => current && current.thread.id === activeId
            ? {
                ...current,
                pendingApproval: null,
                turns: (current.turns ?? []).map((entry) => entry.id === turn.id ? { ...entry, ...turn } : entry),
                thread: {
                  ...current.thread,
                  status: turn.status as ThreadRecord["status"],
                  currentTurnId: turn.id,
                },
              }
            : current);
        }
        void refreshThreads();
      }
    });
    return unsubscribe;
  }, [clearActive, flushPendingItemDeltas, openThread, refreshThreads, scheduleItemDeltaFlush]);

  useEffect(() => {
    if (!isLoomWebRuntime()) return;
    let disposed = false;
    const recovered = () => {
      const threadId = activeIdRef.current;
      if (!threadId) return;
      // Notifications may have been missed while the relay was disconnected.
      threadReadCacheRef.current.delete(threadId);
      void readThread(threadId).then((result) => {
        if (disposed || activeIdRef.current !== threadId) return;
        applyThreadRead(result);
        setConnection("ready");
        setError("");
      }).catch(() => { /* A subsequent reconnect will retry reconciliation. */ });
      void refreshThreads().catch(() => {});
    };
    window.addEventListener("loom:web-host-reconnected", recovered);
    return () => {
      disposed = true;
      window.removeEventListener("loom:web-host-reconnected", recovered);
    };
  }, [applyThreadRead, readThread, refreshThreads]);

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        const bridge = getBridge();
        if (!bridge) {
          throw new Error("Loom preload bridge is unavailable. Check dist-electron/preload.cjs and BrowserWindow.webPreferences.preload.");
        }
        const initialized = await bridge.connect() as InitializeResult;
        if (disposed) return;
        setRuntime(initialized.runtime ?? {});
        await refreshModels();
        if (disposed) return;
        await refreshProjects();
        if (disposed) return;
        threadViewRef.current = "active";
        setThreadViewState("active");
        const list = await refreshThreads("active");
        if (disposed) return;
        setConnection("ready");
        if (list.length) await openThread(list[0].id);
      } catch (cause) {
        if (disposed) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setConnection("error");
      }
    })();
    return () => {
      disposed = true;
    };
  }, [openThread, refreshModels, refreshProjects, refreshThreads]);

  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState !== "visible") return;
      void refreshModels(false).catch(() => {
        // The model manager preserves the last-known-good snapshot on transient
        // provider failures. Polling should therefore stay silent and retry on
        // the next TTL/focus boundary instead of surfacing background noise.
      });
    };
    const timer = window.setInterval(refreshIfVisible, MODEL_CATALOG_POLL_MS);
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [refreshModels]);

  const activeModels = useMemo(
    () => modelsForThread(models, active?.thread),
    [active?.thread, models],
  );

  return useMemo(() => ({
    connection,
    error,
    runtime,
    models: activeModels,
    modelBusy,
    threads,
    projects,
    projectsSupported,
    threadView,
    threadCounts,
    active,
    openingThreadId,
    loadingOlderTurns,
    hasOlderTurns: Boolean(active?.hasMoreTurns),
    items,
    turnActive,
    turnStartedAt,
    context,
    compacting,
    compactionProgress,
    compactContext,
    refreshContext,
    openThread,
    prefetchThread,
    loadOlderTurns,
    newThread,
    renameThread,
    archiveThread,
    deleteThread,
    forkThread,
    setThreadView,
    refreshProjects,
    refreshModels,
    createProject,
    renameProject,
    setProjectInstructions,
    removeProject,
    send,
    interrupt,
    setPermissionMode,
    switchModelProfile,
    switchCurrentModel,
    configureModelProvider,
    addModel,
    deleteModel,
    setReasoning,
    respondApproval,
  }), [
    active,
    addModel,
    archiveThread,
    compacting,
    compactionProgress,
    compactContext,
    connection,
    context,
    configureModelProvider,
    deleteModel,
    deleteThread,
    error,
    forkThread,
    interrupt,
    items,
    modelBusy,
    activeModels,
    newThread,
    openThread,
    openingThreadId,
    loadingOlderTurns,
    prefetchThread,
    loadOlderTurns,
    projects,
    projectsSupported,
    createProject,
    refreshContext,
    refreshModels,
    refreshProjects,
    removeProject,
    renameProject,
    setProjectInstructions,
    renameThread,
    respondApproval,
    runtime,
    send,
    setPermissionMode,
    setReasoning,
    setThreadView,
    switchCurrentModel,
    switchModelProfile,
    threadCounts,
    threads,
    threadView,
    turnActive,
    turnStartedAt,
  ]);
}

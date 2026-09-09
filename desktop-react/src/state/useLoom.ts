import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AddModelInput,
  InitializeResult,
  ModelRestartResult,
  ModelSnapshot,
  ThreadReadResult,
  ThreadRecord,
  TranscriptItem,
  TurnRecord,
} from "../types/loom";

type ThreadView = "active" | "archived";
type ThreadCounts = { active: number; archived: number; all: number };
type ThreadListResult = { threads: ThreadRecord[]; counts?: Partial<ThreadCounts> };

function flattenItems(turns: TurnRecord[]): TranscriptItem[] {
  return turns.flatMap((turn) => turn.items ?? []);
}

function mergeDelta(item: TranscriptItem, delta: Record<string, unknown>): TranscriptItem {
  const next: TranscriptItem = { ...item };
  for (const [key, value] of Object.entries(delta)) {
    if (["text", "stdout", "stderr"].includes(key) && typeof value === "string") {
      next[key] = `${String(next[key] ?? "")}${value}`;
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
  const [threadView, setThreadViewState] = useState<ThreadView>("active");
  const [threadCounts, setThreadCounts] = useState<ThreadCounts>({ active: 0, archived: 0, all: 0 });
  const [active, setActive] = useState<ThreadReadResult | null>(null);
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [turnActive, setTurnActive] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const activeIdRef = useRef("");
  const threadViewRef = useRef<ThreadView>("active");

  useEffect(() => {
    activeIdRef.current = active?.thread.id ?? "";
  }, [active?.thread.id]);

  const clearActive = useCallback(() => {
    activeIdRef.current = "";
    setActive(null);
    setItems([]);
    setTurnActive(false);
    setTurnStartedAt(null);
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

  const refreshModels = useCallback(async () => {
    const snapshot = await requireBridge().listModels<ModelSnapshot>();
    setModels(snapshot);
    return snapshot;
  }, []);

  const openThread = useCallback(async (threadId: string) => {
    const result = await requireBridge().call<ThreadReadResult>("thread/read", { threadId });
    activeIdRef.current = result.thread.id;
    setActive(result);
    setItems(flattenItems(result.turns ?? []));
    const running = threadIsRunning(result.thread);
    setTurnActive(running);
    setTurnStartedAt(running ? turnStartFromRead(result) : null);
  }, []);

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
    await ensureSelection(list);
  }, [ensureSelection, refreshThreads]);

  const newThread = useCallback(async (workspace?: string) => {
    const params = workspace?.trim() ? { workspace: workspace.trim() } : {};
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/start", params);
    threadViewRef.current = "active";
    setThreadViewState("active");
    await refreshThreads("active");
    await openThread(result.thread.id);
  }, [openThread, refreshThreads]);

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

  const send = useCallback(async (input: string) => {
    if (!active?.thread.id || active.thread.archived || !input.trim()) return;
    setTurnActive(true);
    setTurnStartedAt(Date.now());
    try {
      await requireBridge().call("turn/start", { threadId: active.thread.id, input: input.trim() });
    } catch (cause) {
      setTurnActive(false);
      setTurnStartedAt(null);
      throw cause;
    }
  }, [active?.thread.archived, active?.thread.id]);

  const interrupt = useCallback(async () => {
    if (!active?.thread.id) return;
    await requireBridge().call("turn/interrupt", {
      threadId: active.thread.id,
      turnId: active.thread.currentTurnId || undefined,
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
    setRuntime(result.initialization.runtime ?? {});
    setModels(result.models);
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
    setModelBusy(true);
    try {
      const result = await requireBridge().switchModelProfile<ModelRestartResult>(selection);
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [applyModelRestart]);

  const switchCurrentModel = useCallback(async (model: string) => {
    setModelBusy(true);
    try {
      const result = await requireBridge().switchCurrentModel<ModelRestartResult>(model);
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [applyModelRestart]);

  const addModel = useCallback(async (input: AddModelInput) => {
    setModelBusy(true);
    try {
      const result = await requireBridge().addModel<ModelRestartResult>({ ...input });
      await applyModelRestart(result);
    } finally {
      setModelBusy(false);
    }
  }, [applyModelRestart]);

  const respondApproval = useCallback(async (item: TranscriptItem, approved: boolean) => {
    if (!active?.thread.id || !item.callId) return;
    await requireBridge().call("approval/respond", {
      threadId: active.thread.id,
      callId: item.callId,
      approved,
    });
  }, [active?.thread.id]);

  useEffect(() => {
    const bridge = getBridge();
    if (!bridge) {
      setError("Loom preload bridge is unavailable. The renderer started, but Electron did not expose window.loom.");
      setConnection("error");
      return;
    }

    const unsubscribe = bridge.onNotification((message) => {
      const params = message.params ?? {};
      const nestedItem = params.item as Record<string, unknown> | undefined;
      const threadId = String(params.threadId ?? nestedItem?.threadId ?? "");
      const activeId = activeIdRef.current;

      if (message.method === "thread/updated") {
        const thread = params.thread as ThreadRecord | undefined;
        if (thread) {
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
        setThreads((current) => current.filter((entry) => entry.id !== deletedId));
        if (deletedId && deletedId === activeId) clearActive();
        return;
      }
      if (message.method === "thread/started" && threadViewRef.current === "active") void refreshThreads("active");
      if (!activeId || threadId !== activeId) return;

      if (message.method === "item/started") {
        const item = params.item as TranscriptItem | undefined;
        if (item) {
          setTurnActive(true);
          setTurnStartedAt((current) => current ?? Date.now());
          setItems((current) => current.some((entry) => entry.id === item.id) ? current : [...current, item]);
        }
      } else if (message.method === "item/delta") {
        const itemId = String(params.itemId ?? "");
        const delta = (params.delta ?? {}) as Record<string, unknown>;
        setItems((current) => current.map((item) => item.id === itemId ? mergeDelta(item, delta) : item));
      } else if (message.method === "item/completed") {
        const completed = params.item as TranscriptItem | undefined;
        if (completed) {
          setItems((current) => {
            const index = current.findIndex((item) => item.id === completed.id);
            if (index < 0) return [...current, completed];
            const next = [...current];
            next[index] = { ...current[index], ...completed };
            return next;
          });
        }
      } else if (message.method === "thread/resync") {
        void openThread(activeId);
      } else if (message.method === "turn/completed") {
        setTurnActive(false);
        setTurnStartedAt(null);
        void refreshThreads();
      }
    });
    return unsubscribe;
  }, [clearActive, openThread, refreshThreads]);

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
  }, [openThread, refreshModels, refreshThreads]);

  return useMemo(() => ({
    connection,
    error,
    runtime,
    models,
    modelBusy,
    threads,
    threadView,
    threadCounts,
    active,
    items,
    turnActive,
    turnStartedAt,
    openThread,
    newThread,
    renameThread,
    archiveThread,
    deleteThread,
    forkThread,
    setThreadView,
    send,
    interrupt,
    setPermissionMode,
    switchModelProfile,
    switchCurrentModel,
    addModel,
    respondApproval,
  }), [
    active,
    addModel,
    archiveThread,
    connection,
    deleteThread,
    error,
    forkThread,
    interrupt,
    items,
    modelBusy,
    models,
    newThread,
    openThread,
    renameThread,
    respondApproval,
    runtime,
    send,
    setPermissionMode,
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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { InitializeResult, ThreadReadResult, ThreadRecord, TranscriptItem, TurnRecord } from "../types/loom";

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
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [active, setActive] = useState<ThreadReadResult | null>(null);
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [turnActive, setTurnActive] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const activeIdRef = useRef("");

  useEffect(() => {
    activeIdRef.current = active?.thread.id ?? "";
  }, [active?.thread.id]);

  const refreshThreads = useCallback(async () => {
    const result = await requireBridge().call<{ threads: ThreadRecord[] }>("thread/list", { view: "active", limit: 100 });
    const next = result.threads ?? [];
    setThreads(next);
    return next;
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

  const newThread = useCallback(async () => {
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/start", {});
    await refreshThreads();
    await openThread(result.thread.id);
  }, [openThread, refreshThreads]);

  const send = useCallback(async (input: string) => {
    if (!active?.thread.id || !input.trim()) return;
    setTurnActive(true);
    setTurnStartedAt(Date.now());
    try {
      await requireBridge().call("turn/start", { threadId: active.thread.id, input: input.trim() });
    } catch (cause) {
      setTurnActive(false);
      setTurnStartedAt(null);
      throw cause;
    }
  }, [active?.thread.id]);

  const interrupt = useCallback(async () => {
    if (!active?.thread.id) return;
    await requireBridge().call("turn/interrupt", {
      threadId: active.thread.id,
      turnId: active.thread.currentTurnId || undefined,
    });
  }, [active?.thread.currentTurnId, active?.thread.id]);

  const setPermissionMode = useCallback(async (permissionMode: string) => {
    if (!active?.thread.id) return;
    const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/set_permission_mode", {
      threadId: active.thread.id,
      permissionMode,
    });
    const updated = result.thread;
    setThreads((current) => current.map((thread) => (thread.id === updated.id ? updated : thread)));
    setActive((current) => current && current.thread.id === updated.id ? { ...current, thread: updated } : current);
  }, [active?.thread.id]);

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
          setThreads((current) => current.map((entry) => (entry.id === thread.id ? thread : entry)));
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
        setThreads((current) => current.filter((entry) => entry.id !== String(params.threadId ?? "")));
        return;
      }
      if (message.method === "thread/started") void refreshThreads();
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
  }, [openThread, refreshThreads]);

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
        const list = await refreshThreads();
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
  }, [openThread, refreshThreads]);

  return useMemo(() => ({
    connection,
    error,
    runtime,
    threads,
    active,
    items,
    turnActive,
    turnStartedAt,
    openThread,
    newThread,
    send,
    interrupt,
    setPermissionMode,
    respondApproval,
  }), [active, connection, error, interrupt, items, newThread, openThread, respondApproval, runtime, send, setPermissionMode, threads, turnActive, turnStartedAt]);
}

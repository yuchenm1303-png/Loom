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
import { buildApprovalResponse } from "./approvalProtocol";

type ThreadView = "active" | "archived";
type ThreadCounts = { active: number; archived: number; all: number };
type ThreadListResult = { threads: ThreadRecord[]; counts?: Partial<ThreadCounts> };

function flattenItems(turns: TurnRecord[]): TranscriptItem[] {
  return turns.flatMap((turn) => turn.items ?? []);
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

function modelsForThread(snapshot: ModelSnapshot | null, thread?: ThreadRecord | null): ModelSnapshot | null {
  if (!snapshot || !thread?.modelSelection) return snapshot;
  const profile = snapshot.profiles.find((candidate) => candidate.selection === thread.modelSelection);
  if (!profile) return snapshot;
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
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [turnActive, setTurnActive] = useState(false);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [context, setContext] = useState<ContextReport | null>(null);
  const [compactionProgress, setCompactionProgress] = useState<ContextCompactionProgress | null>(null);
  const compacting = compactionProgress?.status === "started" || compactionProgress?.status === "running";
  const activeIdRef = useRef("");
  const threadViewRef = useRef<ThreadView>("active");
  const threadsRef = useRef<ThreadRecord[]>([]);
  const navigationRef = useRef(0);
  const threadListRequestRef = useRef(0);
  const itemIndexRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    activeIdRef.current = active?.thread.id ?? "";
  }, [active?.thread.id]);

  useEffect(() => {
    threadsRef.current = threads;
  }, [threads]);

  const installItems = useCallback((next: TranscriptItem[]) => {
    itemIndexRef.current = buildItemIndex(next);
    setItems(next);
  }, []);

  const clearActive = useCallback(() => {
    navigationRef.current += 1;
    activeIdRef.current = "";
    setActive(null);
    installItems([]);
    setTurnActive(false);
    setTurnStartedAt(null);
    setThreadLoading(false);
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
    const requestId = ++threadListRequestRef.current;
    const result = await requireBridge().call<ThreadListResult>("thread/list", { view, limit: 100 });
    const next = result.threads ?? [];

    // A list request can be slower than a user navigation. Never let an older
    // active/archive refresh overwrite a newer view or a just-created thread.
    if (requestId !== threadListRequestRef.current || view !== threadViewRef.current) return next;

    threadsRef.current = next;
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

  const refreshModels = useCallback(async () => {
    const snapshot = await requireBridge().listModels<ModelSnapshot>();
    setModels(snapshot);
    return snapshot;
  }, []);

  const openThread = useCallback(async (threadId: string) => {
    const targetId = String(threadId || "").trim();
    if (!targetId) return;

    const navigationId = ++navigationRef.current;
    const knownThread = threadsRef.current.find((thread) => thread.id === targetId) ?? null;

    // Switch the visible shell immediately instead of leaving the previous
    // conversation on screen while disk history is being reconstructed.
    activeIdRef.current = targetId;
    setThreadLoading(true);
    setContext(null);
    setCompactionProgress(null);
    if (knownThread) {
      setActive({
        thread: knownThread,
        turns: [],
        pendingApproval: null,
        finalText: "",
        error: "",
      });
      installItems([]);
      const running = threadIsRunning(knownThread);
      setTurnActive(running);
      setTurnStartedAt(running ? Date.now() : null);
    }

    try {
      const result = await requireBridge().call<ThreadReadResult>("thread/read", {
        threadId: targetId,
        includeMessages: false,
        includeEvents: false,
      });
      if (navigationRef.current !== navigationId || activeIdRef.current !== targetId) return;

      activeIdRef.current = result.thread.id;
      setActive(result);
      installItems(flattenItems(result.turns ?? []));
      const running = threadIsRunning(result.thread);
      setTurnActive(running);
      setTurnStartedAt(running ? turnStartFromRead(result) : null);
      void refreshContext(result.thread.id);
    } finally {
      if (navigationRef.current === navigationId && activeIdRef.current === targetId) {
        setThreadLoading(false);
      }
    }
  }, [installItems, refreshContext]);

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

  const newThread = useCallback(async (workspace?: string, projectId?: string) => {
    const navigationId = ++navigationRef.current;
    const params: Record<string, unknown> = projectId?.trim()
      ? { projectId: projectId.trim() }
      : workspace?.trim()
        ? { workspace: workspace.trim() }
        : {};

    setThreadLoading(true);
    try {
      const result = await requireBridge().call<{ thread: ThreadRecord }>("thread/start", params);
      const created = { ...result.thread, archived: false };

      threadViewRef.current = "active";
      setThreadViewState("active");

      // Creating a thread already returns the complete empty-thread record.
      // Put it in the library locally instead of blocking on thread/list and
      // then reading the same empty thread back from disk.
      threadListRequestRef.current += 1;
      setThreads((current) => {
        const existed = current.some((thread) => thread.id === created.id);
        const next = [created, ...current.filter((thread) => thread.id !== created.id)];
        threadsRef.current = next;
        if (!existed) {
          setThreadCounts((counts) => ({
            active: counts.active + 1,
            archived: counts.archived,
            all: counts.all + 1,
          }));
        }
        return next;
      });

      // The user may have clicked another conversation while thread/start was
      // in flight. Keep the created conversation in the sidebar, but do not
      // steal focus back from the newer navigation.
      if (navigationRef.current !== navigationId) return;

      activeIdRef.current = created.id;
      setActive({
        thread: created,
        turns: [],
        pendingApproval: null,
        finalText: "",
        error: "",
      });
      installItems([]);
      setTurnActive(false);
      setTurnStartedAt(null);
      setContext(null);
      setCompactionProgress(null);
      void refreshContext(created.id);
    } finally {
      if (navigationRef.current === navigationId) setThreadLoading(false);
    }
  }, [installItems, refreshContext]);

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
      if (message.method === "runtime/updated") {
        const nextRuntime = params.runtime as InitializeResult["runtime"] | undefined;
        if (nextRuntime) setRuntime((current) => ({ ...current, ...nextRuntime }));
        return;
      }

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
      if (message.method === "turn/started") {
        const turn = params.turn as TurnRecord | undefined;
        if (turn && threadId === activeId) {
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
      if (message.method === "thread/started") {
        const thread = params.thread as ThreadRecord | undefined;
        if (thread && threadViewRef.current === "active" && !thread.archived) {
          threadListRequestRef.current += 1;
          setThreads((current) => {
            const exists = current.some((entry) => entry.id === thread.id);
            const next = exists
              ? current.map((entry) => (entry.id === thread.id ? thread : entry))
              : [thread, ...current];
            threadsRef.current = next;
            return next;
          });
        }
        return;
      }
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
        setItems((current) => {
          const index = indexedItemPosition(itemIndexRef.current, current, itemId);
          if (index < 0) return current;
          const next = [...current];
          next[index] = mergeDelta(current[index], delta);
          return next;
        });
      } else if (message.method === "item/completed") {
        const completed = params.item as TranscriptItem | undefined;
        if (completed) {
          setItems((current) => {
            const index = indexedItemPosition(itemIndexRef.current, current, completed.id);
            if (index < 0) {
              itemIndexRef.current.set(completed.id, current.length);
              return [...current, completed];
            }
            const next = [...current];
            next[index] = { ...current[index], ...completed };
            return next;
          });
          if (completed.type === "approval" && completed.callId) {
            setActive((current) => current && current.pendingApproval?.callId === completed.callId
              ? { ...current, pendingApproval: null }
              : current);
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
        threadViewRef.current = "active";
        setThreadViewState("active");
        const [, , list] = await Promise.all([
          refreshModels(),
          refreshProjects(),
          refreshThreads("active"),
        ]);
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
    items,
    turnActive,
    turnStartedAt,
    threadLoading,
    context,
    compacting,
    compactionProgress,
    compactContext,
    refreshContext,
    openThread,
    newThread,
    renameThread,
    archiveThread,
    deleteThread,
    forkThread,
    setThreadView,
    refreshProjects,
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
    projects,
    projectsSupported,
    createProject,
    refreshContext,
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
    threadLoading,
    turnActive,
    turnStartedAt,
  ]);
}

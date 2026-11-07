/* Weave mode for one conversation: reads projectRun/overview, draws ProjectFlow with the
 * conversation above the hub, and turns its gestures into project commands. Weave is chat
 * first: what the person types is an ordinary turn of the conversation (with the weave_*
 * project tools), and a project starts only from a proposal the agent made, by the person.
 * It polls only while it is shown and the window is visible: every 2 s while the project
 * runs or the agent is answering, every 12 s once it rests. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ProjectFlow } from "./ProjectFlow";
import { raisedBudget, readableError, toConversation, toFlowData, toHistory, toGoal, toModelChoice, toTurnTasks, type ProjectOverview } from "./projectOverview";
import { latestTaskPlan } from "./liveTaskProgress";
import type { ApprovalDecision, ModelSnapshot, TranscriptItem, ProjectRecord } from "../types/loom";
import { ProjectWorkspacePicker } from "./ProjectWorkspacePicker";
import { DUR } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

const LIVE_POLL_MS = 2000;
const REST_POLL_MS = 12000;
/** A read the Host has not answered by then counts as no connection: a frozen or sleeping Host never
 * fails a request, it just never answers, and one read at a time would wait on it forever. */
const READ_TIMEOUT_MS = 20000;
/** Episodes for a new program: generous, since tokens are the budget a person chooses. */
const START_EPISODES = 400;
/** The last overview of each conversation shown here: switching back to 建造 draws the hub at once
 *  (the composer's morph lands on it) and the first read then refreshes it. */
const lastOverview = new Map<string, ProjectOverview>();
/** A new conversation begins: what is written fades this long, then the cards of the old one leave. */
const RENEW_FADE = DUR.fast;
/** What a new conversation shows until its own read arrives: nothing but the project it is in. */
const emptied = (from: ProjectOverview): ProjectOverview => ({ run: null, program: null, children: [], agents: [], events: [], workspace: from.workspace });

/** The hub stays while its conversation changes: what both show stays, the rest leaves and arrives on the hub's own
 *  choreography, and what is written fades out first and in last. */
export function ProjectMode({ threadId, workspace, model, models, onSwitchModel, onReasoning, permissionMode, onPermissionModeChange, items = [], chatting = false, onChat, onInterrupt, imagesAllowed, imageSupport, onApproval, onChangeWorkspace, projects = [], onSelectProject, history, onOpenThread, onNewThread, opening, onRenameThread, onArchiveThread, onDeleteThread }: {
  threadId: string; workspace?: string; model?: string;
  onChangeWorkspace?(path: string): Promise<unknown>;
  projects?: ProjectRecord[]; onSelectProject?(id: string): Promise<unknown>;
  permissionMode?: string; onPermissionModeChange?(mode: string): Promise<unknown>;
  /** The app's model snapshot and switch: a project's agents take the conversation's model. */
  models?: ModelSnapshot | null; onSwitchModel?(selection: string): Promise<unknown>; onReasoning?(kind: string, value: string): Promise<unknown>;
  /** The conversation's transcript and whether its agent is answering now. */
  items?: TranscriptItem[]; chatting?: boolean;
  /** A message to the conversation's agent, as a Weave turn; and stopping its answer. */
  onChat?(input: string, attachments: { path: string; name: string }[], tokens?: number): Promise<unknown>; onInterrupt?(): Promise<unknown> | void;
  /** Whether the model takes images on a message (the chat composer's gate), and whether that is known. */
  imagesAllowed?: boolean; imageSupport?: boolean | null;
  /** Answering a step the agent asks the person to allow (the chat's approval card). */
  onApproval?(item: TranscriptItem, decision: ApprovalDecision, feedback?: string): unknown;
  /** The 建造 conversations for the history card; opening one, or beginning a new one. */
  history?: { id: string; title: string; updatedAt: string; live?: boolean; projectId?: string; workspace?: string }[];
  onOpenThread?(id: string): Promise<unknown> | void; onNewThread?(): Promise<unknown> | void;
  /** The conversation being opened, from the moment it is asked for until it is here. */
  opening?: string;
  onRenameThread?(id: string, title: string): Promise<unknown>; onArchiveThread?(id: string): Promise<unknown>; onDeleteThread?(id: string): Promise<unknown>;
}) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? "zh" : "en";
  const [error, setError] = useState("");
  // A failed refresh is the connection (a Host restart, sleep); it clears itself on the next good read.
  const [offline, setOffline] = useState(false);
  const [loaded, setLoaded] = useState<{ thread: string; overview: ProjectOverview } | null>(null);
  const overview = (loaded?.thread === threadId ? loaded.overview : lastOverview.get(threadId)) ?? null;
  // A new conversation, from this hub. At once what is written fades (`pending`), then the hub shows an empty project
  // (`cleared`) until the new conversation's own read arrives: there is never a frame without a hub.
  const [renewal, setRenewal] = useState<{ from: string; overview: ProjectOverview; cleared: boolean; pending: boolean } | null>(null);
  const [seenThread, setSeenThread] = useState(threadId);
  const knownThreads = useRef(new Set<string>());
  if (seenThread !== threadId) {
    setSeenThread(threadId);
    setError("");
    setOffline(false);
    const before = lastOverview.get(seenThread);
    // Made elsewhere (the sidebar, the shortcut): a conversation no list has held yet, with nothing in it.
    const brandNew = !knownThreads.current.has(threadId) && !items.some((item) => item.threadId === threadId);
    setRenewal((current) => current ? { ...current, cleared: true, pending: false }
      : brandNew && before ? { from: seenThread, overview: emptied(before), cleared: true, pending: false } : null);
  }
  knownThreads.current = new Set(history?.map((entry) => entry.id));
  const [historyRows, setHistoryRows] = useState<Parameters<typeof toHistory>[0] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const historyIds = history?.map(entry => entry.id).sort().join(",");
  useEffect(() => {
    if (historyIds === undefined) return;
    let cancelled = false;
    let pending = false;
    const refresh = async () => {
      if (pending || document.hidden) return;
      pending = true;
      let timer = 0;
      try {
        const result = await Promise.race([
          window.loom.call<{ conversations: Parameters<typeof toHistory>[0] }>("projectWeave/history", {
            threadIds: Array.from(new Set([threadId, ...historyIds.split(",").filter(Boolean)])).slice(0, 200),
          }),
          new Promise<never>((_, reject) => { timer = window.setTimeout(() => reject(new Error("timeout")), READ_TIMEOUT_MS); }),
        ]);
        if (!result?.conversations) throw new Error("history unavailable");
        if (!cancelled) { setHistoryRows(result.conversations); setHistoryError(""); }
      } catch {
        if (!cancelled) setHistoryError(lang === "zh" ? "记录刷新失败，稍后自动重试" : "History could not refresh; retrying shortly");
      } finally { window.clearTimeout(timer); pending = false; }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), REST_POLL_MS);
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => { cancelled = true; window.clearInterval(interval); window.removeEventListener("focus", focus); };
  }, [threadId, historyIds, lang]);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const serial = useRef(0);
  const reading = useRef(false);
  // The first read can take a while on a busy Host; after a moment the empty view says so.
  const [slow, setSlow] = useState(false);

  // One read at a time. A poll or a refocus while one is out waits for the next tick: when a read
  // took longer than the poll, every answer was overtaken by a newer request and dropped, and the
  // hub never appeared. A read after an action (`fresh`) still supersedes the one in flight.
  const load = useCallback(async (fresh = false) => {
    if (reading.current && !fresh) return;
    const ticket = ++serial.current;
    reading.current = true;
    try {
      let timer = 0;
      const result = await Promise.race([
        window.loom.call<ProjectOverview>("projectRun/overview", { threadId }),
        new Promise<never>((_, reject) => { timer = window.setTimeout(() => reject(new Error("timeout")), READ_TIMEOUT_MS); }),
      ]).finally(() => window.clearTimeout(timer));
      if (alive.current && ticket === serial.current) {
        lastOverview.set(threadId, result);
        setLoaded({ thread: threadId, overview: result });
        setRenewal((current) => current && current.from !== threadId ? null : current);
        setNow(Date.now());
        setOffline(false);
      }
    } catch {
      if (alive.current && ticket === serial.current) setOffline(true);
    } finally {
      if (ticket === serial.current) reading.current = false;
    }
  }, [threadId]);

  useEffect(() => {
    alive.current = true;
    reading.current = false;
    void load(true);
    return () => { alive.current = false; };
  }, [load]);
  // Back from a terminal or an editor (a commit made there, say): read again at once.
  useEffect(() => {
    const again = () => { if (!document.hidden) void load(); };
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [load]);

  const resting = !chatting && overview?.goal?.status !== "active" && (!overview?.run || ["integrated", "cancelled"].includes(overview.run.state) ||
    overview.run.state === "needs_review" || overview.run.control === "budget_limited");
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, resting ? REST_POLL_MS : LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, resting]);
  // An answer that ended may have proposed or steered a project: read it now, not at the next rest tick.
  useEffect(() => { if (!chatting) void load(true); }, [chatting, load]);
  const liveConversation = useMemo(() => toConversation(items.filter((item) => item.threadId === threadId), chatting),
                                   [items, threadId, chatting]);
  const turnTasks = useMemo(() => toTurnTasks(latestTaskPlan(items.filter((item) => item.threadId === threadId)), lang),
                            [items, threadId, lang]);
  // Opening another conversation: what is written fades at once, while its overview is read ahead. The hub goes over to
  // it when the fade is done and its overview is known, not before: the conversation it was showing stays until then.
  const [fading, setFading] = useState(false);
  const fadeTicket = useRef(0);
  useEffect(() => {
    if (!opening || opening === threadId) return;
    void window.loom.call<ProjectOverview>("projectRun/overview", { threadId: opening }).then((result) => { lastOverview.set(opening, result); }, () => {});
    if (prefersReducedMotionNow()) return;
    const ticket = ++fadeTicket.current;
    setFading(true);
    window.setTimeout(() => { if (ticket === fadeTicket.current) setFading(false); }, RENEW_FADE);
  }, [opening]);  // eslint-disable-line react-hooks/exhaustive-deps
  const shownThread = useRef(threadId);
  if (!fading && (overview || (renewal && renewal.from !== threadId) || offline)) shownThread.current = threadId;
  const here = shownThread.current === threadId;
  const shownConversation = useRef(liveConversation);
  if (here) shownConversation.current = liveConversation;
  const conversation = shownConversation.current;
  const shownOverview = renewal?.cleared ? renewal.overview : here ? overview : lastOverview.get(shownThread.current) ?? null;
  const waiting = !shownOverview;
  useEffect(() => {
    setSlow(false);
    if (!waiting) return;
    const timer = window.setTimeout(() => setSlow(true), 1500);
    return () => window.clearTimeout(timer);
  }, [waiting]);

  const act = useCallback(async (command: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await command();
      await load(true);
    } catch (cause) {
      setError(readableError(cause instanceof Error ? cause.message : String(cause), lang));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [load, lang]);

  if (!shownOverview) {
    return (
      <div className="pf-loading" aria-busy={!offline}>
        {offline ? (
          <p className="pf-loading-note" role="alert">
            {lang === "en" ? "Could not read this project from the Host." : "没能从 Host 读取这个项目。"}
            <button type="button" onClick={() => void load(true)}>{lang === "en" ? "Try again" : "重试"}</button>
          </p>
        ) : slow ? <p className="pf-loading-note">{lang === "en" ? "Reading the project…" : "正在读取项目…"}</p> : null}
      </div>
    );
  }
  const run = shownOverview.run;
  const control = (action: "pause" | "resume" | "cancel", extra: Record<string, unknown> = {}) => act(() =>
    window.loom.call(`projectRun/${action}`, { operationId: crypto.randomUUID(), runId: run!.id, expectedRevision: run!.revision, ...extra }));
  const workspaceName = (workspace || "").split(/[\\/]/).filter(Boolean).pop() || "";
  const data = { ...toFlowData(shownOverview, now, lang, model || "", workspaceName), models: toModelChoice(models),
                 conversation: renewal?.cleared ? [] : conversation.messages, activity: renewal?.cleared ? "" : conversation.activity,
                 chatting: chatting && here && !renewal?.cleared, conversationPermissionMode: permissionMode,
                 history: history ? toHistory(historyRows || history.filter(entry => entry.id === threadId).map(entry => ({ ...entry, workspace: entry.workspace || (entry.id === threadId ? workspace : undefined) })), threadId, now, lang) : undefined,
                 historyError };
  data.taskList = here && !renewal?.cleared ? turnTasks : [];
  data.goal = here && !renewal?.cleared ? toGoal(shownOverview.goal, lang) : null;
  const proposal = data.phase === "draft" ? shownOverview.proposal : null;
  data.repository.path = run?.repository || workspace;
  if (!run) data.repository.branch = shownOverview.workspace?.branch || "—";
  const programId = shownOverview.program?.id;
  const workspaceLocked = Boolean(run) || chatting || busy;
  const workspaceReady = !shownOverview.workspace?.status || shownOverview.workspace.status === "ready";
  const workBranch = `codex/build-${threadId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 12)}`;

  const chooseWorkspace = !run && !chatting && onChangeWorkspace && window.loom.pickDirectory ? () => void act(async () => {
    const picked = await window.loom.pickDirectory!();
    if (picked) await onChangeWorkspace(picked);
  }) : undefined;

  const changing = !here || Boolean(opening && opening !== threadId);
  const startNew = onNewThread ? async () => {
    if (renewal?.pending) return;
    const from = threadId, emptyHub = emptied(shownOverview);
    setError("");
    const still = prefersReducedMotionNow();
    setRenewal({ from, overview: emptyHub, cleared: still, pending: true });
    const fade = still ? 0 : window.setTimeout(() => setRenewal((current) => current?.from === from ? { ...current, cleared: true } : current), RENEW_FADE);
    try {
      await onNewThread();
      setRenewal((current) => current && { ...current, pending: false });
    } catch (cause) {
      // Nothing was created: what the hub showed comes back.
      window.clearTimeout(fade);
      setRenewal(null);
      setError(readableError(cause instanceof Error ? cause.message : String(cause), lang));
    }
  } : undefined;

  return (
    <ProjectFlow lang={lang} data={data} conversationId={shownThread.current} brandNew={Boolean(renewal)} renewing={Boolean(renewal && (renewal.pending || !renewal.cleared)) || changing} actions={{
      workspaceControls: <ProjectWorkspacePicker projects={projects} workspace={{ ...shownOverview.workspace, path: data.repository.path }}
        locked={workspaceLocked} lang={lang} branch={workBranch}
        onSelect={onSelectProject ? id => void act(() => onSelectProject(id)) : undefined} onBrowse={chooseWorkspace}
        onPrepare={!workspaceLocked ? action => void act(() => window.loom.call("projectRun/prepareWorkspace", {
          threadId, action, confirmed: true, expectedPath: shownOverview.workspace?.path, branch: workBranch })) : undefined} />,
      onChooseWorkspace: chooseWorkspace,
      // While the hub goes over, what it shows is no longer what its buttons would reach.
      busy: busy || changing,
      error: error || (offline ? (lang === "en" ? "Lost the Host; reconnecting…" : "与 Host 的连接中断，正在重连…") : ""),
      onPickFiles: window.loom.pickFiles ? () => window.loom.pickFiles() : undefined,
      onAttach: programId ? (paths) => act(() => window.loom.call("projectProgram/attach", { programId, attachments: paths })) : undefined,
      onModel: onSwitchModel ? (selection) => act(() => onSwitchModel(selection)) : undefined,
      // Its own errors show on the scale; the hub's busy state is for the run, not this.
      onReasoning: onReasoning ? async (kind, value) => { await onReasoning(kind, value); } : undefined,
      onCommitWorkspace: (message, count) => act(() => window.loom.call("projectRun/commitWorkspace", { threadId, message, expectedCount: count })),
      // The proposal the agent wrote, started by the person with the budget and agents set in the footer.
      onStart: proposal && workspaceReady ? (_objective, workers, tokens, attachments, fullAccess) => act(() => window.loom.call("projectWeave/start", {
        attachments, operationId: `weave:${proposal.id}`, threadId, proposalId: proposal.id, tokenBudget: tokens,
        maxEpisodes: START_EPISODES, maxWorkers: workers, maxMilestones: 20, permissionMode: fullAccess ? "full-access" : "workspace" })) : undefined,
      onDismiss: proposal ? () => act(() => window.loom.call("projectWeave/dismiss", { threadId, proposalId: proposal.id })) : undefined,
      imagesAllowed, imageSupport,
      onGoal: (action) => act(() => window.loom.call("projectWeave/goal", { threadId, action })),
      onChat: onChat ? (text, attachments, tokens) => { setError(""); void onChat(text, attachments, tokens).catch((cause) => setError(readableError(cause instanceof Error ? cause.message : String(cause), lang))); } : undefined,
      onInterrupt: onInterrupt ? () => { void onInterrupt(); } : undefined,
      onPermissionModeChange,
      onOpenThread: onOpenThread ? (id) => onOpenThread(id) : undefined,
      onNewThread: startNew,
      // The card shows these failures on its own rows; the hub's busy state stays the run's.
      onRenameThread, onArchiveThread, onDeleteThread,
      onApproval: onApproval ? (item, decision, feedback) => { void onApproval(item, decision, feedback); } : undefined,
      onPause: () => run && control("pause"),
      onResume: () => run && control("resume"),
      onCancel: () => run && control("cancel"),
      // Out of budget: add half again, the same step the panel offers; otherwise review the stopped work again and resume.
      // An agent waiting for approval: the person allows that one call or declines it.
      onAttention: (choice) => run && (data.attention?.action === "approval"
        ? act(() => window.loom.call("projectRun/approval", { runId: run.id, sessionId: data.attention!.agentId, decision: choice }))
        : data.attention?.action === "resume"
        ? control("resume")
        : data.attention?.action === "budget"
        ? control("resume", raisedBudget(run, shownOverview.program))
        : act(() => window.loom.call("projectRun/recover", { operationId: crypto.randomUUID(), runId: run.id }))),
    }} />
  );
}

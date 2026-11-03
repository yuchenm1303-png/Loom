/* Weave mode for one conversation: reads projectRun/overview, draws ProjectFlow with the
 * conversation above the hub, and turns its gestures into project commands. Weave is chat
 * first: what the person types is an ordinary turn of the conversation (with the weave_*
 * project tools), and a project starts only from a proposal the agent made, by the person.
 * It polls only while it is shown and the window is visible: every 2 s while the project
 * runs or the agent is answering, every 12 s once it rests. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { ProjectFlow } from "./ProjectFlow";
import { raisedBudget, readableError, toConversation, toFlowData, toHistory, toModelChoice, type ProjectOverview } from "./projectOverview";
import type { ApprovalDecision, ModelSnapshot, TranscriptItem, ProjectRecord } from "../types/loom";
import { ProjectWorkspacePicker } from "./ProjectWorkspacePicker";

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

export function ProjectMode({ threadId, workspace, model, models, onSwitchModel, onReasoning, items = [], chatting = false, onChat, onInterrupt, imagesAllowed, imageSupport, onApproval, onChangeWorkspace, projects = [], onSelectProject, history, onOpenThread, onNewThread }: {
  threadId: string; workspace?: string; model?: string;
  onChangeWorkspace?(path: string): Promise<unknown>;
  projects?: ProjectRecord[]; onSelectProject?(id: string): Promise<unknown>;
  /** The app's model snapshot and switch: a project's agents take the conversation's model. */
  models?: ModelSnapshot | null; onSwitchModel?(selection: string): Promise<unknown>; onReasoning?(kind: string, value: string): Promise<unknown>;
  /** The conversation's transcript and whether its agent is answering now. */
  items?: TranscriptItem[]; chatting?: boolean;
  /** A message to the conversation's agent, as a Weave turn; and stopping its answer. */
  onChat?(input: string, attachments: { path: string; name: string }[]): Promise<unknown>; onInterrupt?(): Promise<unknown> | void;
  /** Whether the model takes images on a message (the chat composer's gate), and whether that is known. */
  imagesAllowed?: boolean; imageSupport?: boolean | null;
  /** Answering a step the agent asks the person to allow (the chat's approval card). */
  onApproval?(item: TranscriptItem, decision: ApprovalDecision, feedback?: string): unknown;
  /** The 建造 conversations for the history card; opening one, or beginning a new one. */
  history?: { id: string; title: string; updatedAt: string; live?: boolean }[];
  onOpenThread?(id: string): Promise<unknown> | void; onNewThread?(): Promise<unknown> | void;
}) {
  const { language } = useI18n();
  const lang = language === "zh-CN" ? "zh" : "en";
  const [overview, setOverview] = useState<ProjectOverview | null>(() => lastOverview.get(threadId) ?? null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A failed refresh is the connection (a Host restart, sleep); it clears itself on the next good read.
  const [offline, setOffline] = useState(false);
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
        setOverview(result);
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
    setOverview(lastOverview.get(threadId) ?? null);
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

  const resting = !chatting && (!overview?.run || ["integrated", "cancelled"].includes(overview.run.state) ||
    overview.run.state === "needs_review" || overview.run.control === "budget_limited");
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, resting ? REST_POLL_MS : LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, resting]);
  // An answer that ended may have proposed or steered a project: read it now, not at the next rest tick.
  useEffect(() => { if (!chatting) void load(true); }, [chatting, load]);
  const conversation = useMemo(() => toConversation(items.filter((item) => item.threadId === threadId), chatting),
                               [items, threadId, chatting]);
  const waiting = !overview;
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

  if (!overview) {
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
  const run = overview.run;
  const control = (action: "pause" | "resume" | "cancel", extra: Record<string, unknown> = {}) => act(() =>
    window.loom.call(`projectRun/${action}`, { operationId: crypto.randomUUID(), runId: run!.id, expectedRevision: run!.revision, ...extra }));
  const workspaceName = (workspace || "").split(/[\\/]/).filter(Boolean).pop() || "";
  const data = { ...toFlowData(overview, now, lang, model || "", workspaceName), models: toModelChoice(models),
                 conversation: conversation.messages, activity: conversation.activity, chatting,
                 history: history ? toHistory(history, threadId, now, lang) : undefined };
  const proposal = data.phase === "draft" ? overview.proposal : null;
  data.repository.path = run?.repository || workspace;
  if (!run) data.repository.branch = overview.workspace?.branch || "—";
  const programId = overview.program?.id;
  const workspaceLocked = Boolean(run) || chatting || busy;
  const workspaceReady = !overview.workspace?.status || overview.workspace.status === "ready";
  const workBranch = `codex/build-${threadId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 12)}`;

  return (
    <ProjectFlow lang={lang} data={data} actions={{
      workspaceControls: <ProjectWorkspacePicker projects={projects} workspace={{ ...overview.workspace, path: data.repository.path }}
        locked={workspaceLocked} lang={lang} branch={workBranch}
        onSelect={onSelectProject ? id => void act(() => onSelectProject(id)) : undefined}
        onPrepare={!workspaceLocked ? action => void act(() => window.loom.call("projectRun/prepareWorkspace", {
          threadId, action, confirmed: true, expectedPath: overview.workspace?.path, branch: workBranch })) : undefined} />,
      onChooseWorkspace: !run && !chatting && onChangeWorkspace && window.loom.pickDirectory ? () => void act(async () => {
        const picked = await window.loom.pickDirectory!();
        if (picked) await onChangeWorkspace(picked);
      }) : undefined,
      busy,
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
      onChat: onChat ? (text, attachments) => { setError(""); void onChat(text, attachments).catch((cause) => setError(readableError(cause instanceof Error ? cause.message : String(cause), lang))); } : undefined,
      onInterrupt: onInterrupt ? () => { void onInterrupt(); } : undefined,
      onOpenThread: onOpenThread ? (id) => { void onOpenThread(id); } : undefined,
      onNewThread: onNewThread ? () => act(async () => { await onNewThread(); }) : undefined,
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
        ? control("resume", raisedBudget(run, overview.program))
        : act(() => window.loom.call("projectRun/recover", { operationId: crypto.randomUUID(), runId: run.id }))),
    }} />
  );
}

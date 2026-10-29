import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Check, CircleAlert, MessagesSquare, PanelRight, X } from "./icons";
import { useLoomLanguage } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import {
  dismissConversationActivity, seedConversationActivity, useConversationActivity, type ConversationActivity,
} from "../state/conversationActivity";
import { withThreadReadTimeout } from "../state/threadReadTimeout";
import { endThreadDrag, startThreadDrag } from "../threadDrag";
import { describeRow } from "./activityModel";
import { runtimeCopy } from "./runtimeCopy";
import { formatStepDuration } from "./stepFormat";
import { PanelHeader } from "./PanelHeader";
import { PanelEmpty } from "./PanelEmpty";
import "./conversation-runs.css";

interface ConversationRunsPanelProps {
  open: boolean;
  threads: ThreadRecord[];
  projects: ProjectRecord[];
  /** The conversation on screen; the panel is about the others. */
  activeId?: string;
  /** The conversation in the split pane, if one is open. */
  splitId?: string;
  onClose(): void;
  onOpen(threadId: string): void;
  onSplit(threadId: string): void;
}

const COPY = {
  "zh-CN": {
    title: "对话动态",
    close: "关闭对话动态",
    subtitle: (live: number, done: number) => live || done
      ? [live ? `${live} 个进行中` : "", done ? `${done} 个刚完成` : ""].filter(Boolean).join(" · ")
      : "其他对话都空闲",
    emptyTitle: "其他对话都空闲",
    emptyCopy: "在别的对话里开始任务后，它的进度会出现在这里，不用切过去也能看到。",
    running: "运行中",
    paused: "已暂停",
    approval: "等你批准",
    completed: "已完成",
    failed: "未能完成",
    stopped: "已停止",
    thinking: "正在思考",
    replying: "正在撰写回复",
    approvalHint: "有一个操作需要你批准",
    steps: (count: number) => `${count} 步`,
    open: "打开",
    split: "在分屏中打开",
    inSplit: "分屏中",
    dismiss: "移除",
    clear: "清除已完成",
    hint: "拖动对话到工作区右侧可分屏查看",
  },
  en: {
    title: "Conversations",
    close: "Close conversations",
    subtitle: (live: number, done: number) => live || done
      ? [live ? `${live} running` : "", done ? `${done} just finished` : ""].filter(Boolean).join(" · ")
      : "Every other conversation is idle",
    emptyTitle: "Every other conversation is idle",
    emptyCopy: "When another conversation is working, its progress shows up here, without switching to it.",
    running: "Running",
    paused: "Paused",
    approval: "Needs approval",
    completed: "Done",
    failed: "Didn't finish",
    stopped: "Stopped",
    thinking: "Thinking",
    replying: "Writing the reply",
    approvalHint: "An action is waiting for your approval",
    steps: (count: number) => `${count} step${count === 1 ? "" : "s"}`,
    open: "Open",
    split: "Open in split view",
    inSplit: "In split view",
    dismiss: "Dismiss",
    clear: "Clear finished",
    hint: "Drag a conversation to the right of the workspace to split",
  },
} as const;

type RowState = "approval" | "running" | "paused" | "completed" | "failed" | "stopped";

interface Row {
  thread: ThreadRecord;
  state: RowState;
  activity: ConversationActivity | undefined;
}

function stateOf(thread: ThreadRecord, activity?: ConversationActivity): RowState | null {
  if (thread.status === "waiting_approval") return "approval";
  if (thread.status === "running") return thread.paused ? "paused" : "running";
  if (!activity?.endedAt || activity.seen) return null;
  const status = activity.endStatus || String(thread.status);
  if (status === "completed") return "completed";
  if (status === "cancelled" || status === "interrupted" || status === "stopped") return "stopped";
  return "failed";
}

const RANK: Record<RowState, number> = { approval: 0, running: 1, paused: 2, failed: 3, completed: 4, stopped: 5 };

function placeOf(thread: ThreadRecord, projects: ProjectRecord[]): string {
  const project = thread.projectId ? projects.find(entry => entry.id === thread.projectId) : undefined;
  if (project?.name) return project.name;
  return String(thread.workspace || "").replaceAll("\\", "/").replace(/\/+$/, "").split("/").filter(Boolean).at(-1) ?? "";
}

/** Presentation only: the conversation list says who runs, the activity store says what they are doing. */
export function ConversationRunsPanel({ open, threads, projects, activeId, splitId, onClose, onOpen, onSplit }: ConversationRunsPanelProps) {
  const language = useLoomLanguage();
  const copy = COPY[language];
  const presence = useMotionPresence(open, RETAIN.base);
  const activity = useConversationActivity();
  const steps = useMemo(() => runtimeCopy(language), [language]);
  const [now, setNow] = useState(() => Date.now());
  const seeded = useRef(new Set<string>());

  const rows = useMemo(() => threads
    .filter(thread => !thread.archived && thread.id !== activeId)
    .flatMap((thread): Row[] => {
      const record = activity.get(thread.id);
      const state = stateOf(thread, record);
      return state ? [{ thread, activity: record, state }] : [];
    })
    .sort((a, b) => RANK[a.state] - RANK[b.state]
      || (b.activity?.endedAt ?? 0) - (a.activity?.endedAt ?? 0)
      || (a.activity?.startedAt ?? 0) - (b.activity?.startedAt ?? 0)), [activeId, activity, threads]);
  const live = rows.filter(row => row.state === "running" || row.state === "approval" || row.state === "paused").length;
  const done = rows.length - live;

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, open]);

  // The elapsed times count while the panel is open and something is running; nothing ticks behind a closed panel.
  useEffect(() => {
    if (!open || !live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live, open]);

  // A conversation that was already running when Loom started: one read of its last turn gives its start and step.
  useEffect(() => {
    if (!open) return;
    rows.forEach(({ thread, activity: record }) => {
      if (thread.status !== "running" && thread.status !== "waiting_approval") return;
      if (record?.startedAt && record.latest) return;
      const key = `${thread.id}:${thread.currentTurnId ?? ""}`;
      if (seeded.current.has(key)) return;
      seeded.current.add(key);
      void withThreadReadTimeout(window.loom.call<{ turns?: Array<Parameters<typeof seedConversationActivity>[1]> }>("thread/read", {
        threadId: thread.id, presentationOnly: true, turnLimit: 1,
      })).then(result => seedConversationActivity(thread.id, result.turns?.at(-1))).catch(() => {
        // The row still shows the conversation's state; only its step and clock stay unknown.
      });
    });
  }, [open, rows]);

  if (!presence.mounted) return null;

  const detail = (row: Row): { text: string; title?: string } => {
    if (row.state === "approval") return { text: copy.approvalHint };
    const record = row.activity;
    if (row.state === "running" && record?.replying) return { text: copy.replying };
    const latest = record?.latest;
    if (latest) {
      const description = describeRow({ key: latest.id, item: latest, wrapper: null }, steps);
      const text = [row.state === "running" || row.state === "paused" ? description.label : description.verb, description.target]
        .filter(Boolean).join(" ");
      return { text, title: description.title ?? text };
    }
    if (row.state === "running") return { text: copy.thinking };
    return { text: copy[row.state] };
  };

  const clock = (row: Row): string => {
    const record = row.activity;
    if (!record?.startedAt) return "";
    const end = record.endedAt || (row.state === "running" || row.state === "approval" || row.state === "paused" ? now : 0);
    return end ? formatStepDuration(Math.max(0, end - record.startedAt)) : "";
  };

  return createPortal(
    <section className="conversation-runs-panel pl-float pl-reveal" data-motion-phase={presence.phase}
      data-conversation-runs="true" inert={!open} role="dialog" aria-modal="false" aria-label={copy.title}>
      <PanelHeader
        className="conversation-runs-header"
        mark={<><MessagesSquare size={16} strokeWidth={1.8} />{live ? <span className="pl-pulse is-live" aria-hidden="true" /> : null}</>}
        title={copy.title}
        subtitle={copy.subtitle(live, done)}
        meta={rows.length ? <span className={`pl-chip${live ? " is-live" : ""}`}>{live || done}</span> : undefined}
        closeLabel={copy.close}
        onClose={onClose}
      />
      {rows.length ? (
        <ul className="conversation-runs-list" aria-label={copy.title}>
          {rows.map(row => {
            const { thread } = row;
            const info = detail(row);
            const time = clock(row);
            const place = placeOf(thread, projects);
            const ended = row.state === "completed" || row.state === "failed" || row.state === "stopped";
            const inSplit = thread.id === splitId;
            return (
              <li key={thread.id} className={`conversation-run is-${row.state}`} data-thread-id={thread.id}
                draggable
                onDragStart={event => startThreadDrag(event, thread.id, thread.title)}
                onDragEnd={endThreadDrag}>
                <button type="button" className="conversation-run-main" onClick={() => onOpen(thread.id)}
                  title={`${thread.title}${info.title ? ` · ${info.title}` : ""}`}>
                  <span className="conversation-run-mark" aria-hidden="true">
                    {row.state === "completed" ? <Check size={12} strokeWidth={2.2} />
                      : row.state === "failed" ? <CircleAlert size={12} strokeWidth={2} />
                      : row.state === "stopped" ? <X size={11} strokeWidth={2.2} /> : null}
                  </span>
                  <span className="conversation-run-body">
                    <span className="conversation-run-top">
                      <span className="conversation-run-title">{thread.title}</span>
                      <span className="conversation-run-state">{inSplit ? copy.inSplit : copy[row.state]}</span>
                    </span>
                    <span className="conversation-run-step">{info.text}</span>
                    <span className="conversation-run-meta">
                      {place ? <span className="conversation-run-place">{place}</span> : null}
                      {row.activity?.steps ? <span>{copy.steps(row.activity.steps)}</span> : null}
                      {time ? <span className="conversation-run-time">{time}</span> : null}
                    </span>
                  </span>
                </button>
                <span className="conversation-run-actions">
                  {!inSplit ? (
                    <button type="button" className="pl-icon-button" aria-label={copy.split} title={copy.split}
                      onClick={() => onSplit(thread.id)}><PanelRight size={14} strokeWidth={1.8} /></button>
                  ) : null}
                  <button type="button" className="pl-icon-button" aria-label={copy.open} title={copy.open}
                    onClick={() => onOpen(thread.id)}><ArrowUpRight size={14} strokeWidth={1.8} /></button>
                  {ended ? (
                    <button type="button" className="pl-icon-button" aria-label={copy.dismiss} title={copy.dismiss}
                      onClick={() => dismissConversationActivity(thread.id)}><X size={13} strokeWidth={1.8} /></button>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <PanelEmpty className="conversation-runs-empty" icon={<MessagesSquare size={18} strokeWidth={1.7} />}
          title={copy.emptyTitle} copy={copy.emptyCopy} />
      )}
      <footer className="conversation-runs-foot">
        <span>{copy.hint}</span>
        {done ? <button type="button" className="conversation-runs-clear" onClick={() => dismissConversationActivity()}>{copy.clear}</button> : null}
      </footer>
    </section>,
    document.body,
  );
}

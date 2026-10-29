import {
  useEffect, useLayoutEffect, useRef, useState,
  type CSSProperties, type DragEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent,
} from "react";
import { ArrowUp, LoaderCircle, Maximize2, PanelRight, Square } from "./icons";
import { carriesThread, droppedThread, endThreadDrag } from "../threadDrag";
import { useLoomLanguage } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import { useCompanionThread } from "../state/useCompanionThread";
import type { ApprovalDecision, ProjectRecord, TranscriptItem } from "../types/loom";
import { PanelHeader } from "./PanelHeader";
import { Transcript } from "./Transcript";
import "./split-conversation.css";

interface SplitConversationPaneProps {
  threadId: string;
  projects: ProjectRecord[];
  /** Share of the workspace the pane takes (0..1). */
  ratio: number;
  onRatioChange(ratio: number): void;
  onRatioReset(): void;
  onClose(): void;
  /** Swap: this conversation becomes the main one and the main one moves here. */
  onPromote(threadId: string): void;
}

export const SPLIT_MIN = 0.28;
export const SPLIT_MAX = 0.72;
export const SPLIT_DEFAULT = 0.46;

const COPY = {
  "zh-CN": {
    label: "分屏对话",
    close: "关闭分屏",
    promote: "与主对话互换",
    running: "运行中",
    approval: "等你批准",
    paused: "已暂停",
    completed: "已完成",
    failed: "未能完成",
    idle: "空闲",
    loading: "正在载入对话…",
    empty: "这个对话还没有消息",
    reply: "回复这个对话…",
    steer: "引导当前任务…",
    send: "发送",
    stop: "停止",
    resize: "拖动调整分屏宽度 · 双击恢复",
  },
  en: {
    label: "Split conversation",
    close: "Close split view",
    promote: "Swap with the main conversation",
    running: "Running",
    approval: "Needs approval",
    paused: "Paused",
    completed: "Done",
    failed: "Didn't finish",
    idle: "Idle",
    loading: "Loading the conversation…",
    empty: "No messages in this conversation yet",
    reply: "Reply to this conversation…",
    steer: "Guide the current task…",
    send: "Send",
    stop: "Stop",
    resize: "Drag to resize the split · Double-click to reset",
  },
} as const;

const DROP_COPY = {
  "zh-CN": { main: "在此打开", split: "在右侧分屏打开", replace: "替换分屏中的对话" },
  en: { main: "Open here", split: "Open beside it", replace: "Replace the split conversation" },
} as const;

/**
 * Where a carried conversation can land. It is always in the workspace's grid area and only takes the pointer while a
 * conversation is being dragged (threadDrag.ts marks the document then), so it never stands in the way otherwise.
 */
export function SplitDropTarget({ splitOpen, onOpen, onSplit }: {
  splitOpen: boolean;
  onOpen(threadId: string): void;
  onSplit(threadId: string): void;
}) {
  const copy = DROP_COPY[useLoomLanguage()];
  const [over, setOver] = useState<"main" | "split" | null>(null);

  // A drag that ends anywhere (dropped elsewhere, cancelled with Escape, its source gone) puts the zone away.
  useEffect(() => {
    const done = () => { endThreadDrag(); setOver(null); };
    window.addEventListener("dragend", done, true);
    window.addEventListener("drop", done, true);
    return () => {
      window.removeEventListener("dragend", done, true);
      window.removeEventListener("drop", done, true);
    };
  }, []);

  const half = (side: "main" | "split") => ({
    "data-over": over === side ? "true" : undefined,
    onDragEnter: (event: DragEvent<HTMLDivElement>) => { if (carriesThread(event)) setOver(side); },
    onDragOver: (event: DragEvent<HTMLDivElement>) => {
      if (!carriesThread(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      if (over !== side) setOver(side);
    },
    onDragLeave: (event: DragEvent<HTMLDivElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(current => (current === side ? null : current));
    },
    onDrop: (event: DragEvent<HTMLDivElement>) => {
      const threadId = droppedThread(event);
      event.preventDefault();
      endThreadDrag();
      setOver(null);
      if (!threadId) return;
      if (side === "main") onOpen(threadId);
      else onSplit(threadId);
    },
  });

  return (
    <div className="split-drop-target" aria-hidden="true">
      <div className="split-drop-half is-main" {...half("main")}>
        <Maximize2 size={18} strokeWidth={1.7} /><span>{copy.main}</span>
      </div>
      <div className="split-drop-half is-split" {...half("split")}>
        <PanelRight size={18} strokeWidth={1.7} /><span>{splitOpen ? copy.replace : copy.split}</span>
      </div>
    </div>
  );
}

/** How close to the bottom still counts as "following" the conversation. */
const FOLLOW_SLACK = 48;

export function SplitConversationPane({ threadId, projects, ratio, onRatioChange, onRatioReset, onClose, onPromote }: SplitConversationPaneProps) {
  const language = useLoomLanguage();
  const copy = COPY[language];
  const presence = useMotionPresence(Boolean(threadId), RETAIN.base);
  // The pane keeps showing the conversation it had while it leaves.
  const [shownId, setShownId] = useState(threadId);
  if (threadId && threadId !== shownId) setShownId(threadId);
  const companion = useCompanionThread(presence.mounted ? shownId : "");
  const { thread, items, running } = companion;
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const stageRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const resize = useRef<{ pointerId: number; left: number; width: number; share: number; boxes: HTMLElement[] } | null>(null);

  useEffect(() => { setDraft(""); setError(""); following.current = true; }, [shownId]);

  // Follow the bottom while the reader is there; a reader who scrolled up keeps their place.
  useEffect(() => {
    const stage = stageRef.current;
    const scroller = stage?.querySelector<HTMLElement>(".transcript-scroll");
    if (!stage || !scroller) return;
    const track = () => {
      following.current = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < FOLLOW_SLACK;
    };
    const stick = () => { if (following.current) scroller.scrollTop = scroller.scrollHeight; };
    scroller.addEventListener("scroll", track, { passive: true });
    const content = scroller.querySelector("main.transcript");
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(stick) : null;
    if (content) observer?.observe(content);
    stick();
    return () => { scroller.removeEventListener("scroll", track); observer?.disconnect(); };
  }, [shownId, items.length > 0]);

  useLayoutEffect(() => {
    const scroller = stageRef.current?.querySelector<HTMLElement>(".transcript-scroll");
    if (scroller && following.current) scroller.scrollTop = scroller.scrollHeight;
  }, [items]);

  // The field grows with what is typed, up to six lines.
  useLayoutEffect(() => {
    const field = fieldRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 132)}px`;
  }, [draft]);

  if (!presence.mounted) return null;

  const status = String(thread?.status ?? "");
  const state = status === "waiting_approval" ? "approval"
    : status === "running" ? (thread?.paused ? "paused" : "running")
    : status === "completed" ? "completed"
    : status === "failed" ? "failed" : "idle";
  const project = thread?.projectId ? projects.find(entry => entry.id === thread.projectId) : undefined;
  const place = project?.name || String(thread?.workspace || "").replaceAll("\\", "/").split("/").filter(Boolean).at(-1) || "";

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError("");
    try {
      await companion.send(text);
      setDraft("");
      following.current = true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  };

  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit();
  };

  const approve = (item: TranscriptItem, decision: ApprovalDecision, feedback?: string) => {
    void companion.respondApproval(item, decision, feedback).catch(cause => {
      setError(cause instanceof Error ? cause.message : String(cause));
    });
  };

  // --- The divider: the pane's left edge, carried by the pointer or the arrow keys ---------------------------------
  // While the pointer carries it, the share is written straight onto the two boxes that read it (the workspace and the
  // pane; the property is not inherited, so nothing else restyles) and handed to the app once, on release.
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const pane = event.currentTarget.parentElement;
    const workspace = pane?.parentElement?.querySelector<HTMLElement>(":scope > .workspace");
    if (!workspace || !pane) return;
    const left = workspace.getBoundingClientRect().left;
    const width = pane.getBoundingClientRect().right - left;
    resize.current = { pointerId: event.pointerId, left, width, share: ratio, boxes: [workspace, pane] };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.documentElement.dataset.loomSplitResizing = "true";
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = resize.current;
    if (!session || session.pointerId !== event.pointerId || session.width <= 0) return;
    session.share = Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, 1 - (event.clientX - session.left) / session.width));
    session.boxes.forEach(box => box.style.setProperty("--loom-split-ratio", String(session.share)));
  };
  const endResize = () => {
    const session = resize.current;
    resize.current = null;
    delete document.documentElement.dataset.loomSplitResizing;
    if (session && session.share !== ratio) onRatioChange(session.share);
  };
  const keyResize = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 0.08 : 0.02;
    if (event.key === "ArrowLeft") onRatioChange(Math.min(SPLIT_MAX, ratio + step));
    else if (event.key === "ArrowRight") onRatioChange(Math.max(SPLIT_MIN, ratio - step));
    else if (event.key === "Home" || event.key === "Enter") onRatioReset();
    else return;
    event.preventDefault();
  };

  return (
    <section className="split-pane pl-dock" data-motion-phase={presence.phase} data-split-thread={shownId}
      data-run-state={state} aria-label={copy.label} inert={!threadId}
      style={{ "--loom-split-ratio": ratio } as CSSProperties}>
      <div className="split-pane-resizer" role="separator" aria-orientation="vertical" tabIndex={0}
        aria-label={copy.resize} title={copy.resize}
        aria-valuemin={Math.round(SPLIT_MIN * 100)} aria-valuemax={Math.round(SPLIT_MAX * 100)} aria-valuenow={Math.round(ratio * 100)}
        onPointerDown={startResize} onPointerMove={moveResize} onPointerUp={endResize} onPointerCancel={endResize}
        onDoubleClick={onRatioReset} onKeyDown={keyResize} />
      <PanelHeader
        className="split-pane-header"
        mark={<span className="split-pane-state" aria-hidden="true" />}
        markClassName="split-pane-mark"
        title={thread?.title || copy.loading}
        titleHint={thread?.title}
        subtitle={[place, copy[state]].filter(Boolean).join(" · ")}
        actions={thread ? (
          <button type="button" className="pl-icon-button split-pane-promote" aria-label={copy.promote} title={copy.promote}
            onClick={() => onPromote(shownId)}><Maximize2 size={14} strokeWidth={1.8} /></button>
        ) : null}
        closeLabel={copy.close}
        closeClassName="split-pane-close"
        onClose={onClose}
      />
      <div className="split-pane-stage" ref={stageRef}>
        {items.length ? (
          <Transcript
            threadId={shownId}
            items={items}
            running={running}
            currentTurnId={running ? thread?.currentTurnId : undefined}
            workspace={thread?.workspace}
            promptDisabled={sending}
            onPrompt={text => companion.send(text)}
            onApproval={approve}
          />
        ) : (
          <div className="split-pane-placeholder">
            {companion.loading || !thread ? <LoaderCircle size={16} className="split-pane-spinner" aria-hidden="true" /> : null}
            <span>{companion.error || (companion.loading || !thread ? copy.loading : copy.empty)}</span>
          </div>
        )}
      </div>
      <form className="split-pane-composer" onSubmit={event => { event.preventDefault(); void submit(); }}>
        {error ? <p className="split-pane-error" role="alert">{error}</p> : null}
        <div className="split-pane-field">
          <textarea ref={fieldRef} rows={1} value={draft} disabled={!thread || Boolean(thread.archived)}
            placeholder={running ? copy.steer : copy.reply} aria-label={running ? copy.steer : copy.reply}
            onChange={event => setDraft(event.target.value)} onKeyDown={handleKey} />
          {running && !draft.trim() ? (
            <button type="button" className="split-pane-action is-stop" aria-label={copy.stop} title={copy.stop}
              onClick={() => void companion.interrupt().catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))}>
              <Square size={11} strokeWidth={2.4} fill="currentColor" />
            </button>
          ) : (
            <button type="submit" className="split-pane-action" aria-label={copy.send} title={copy.send}
              disabled={!draft.trim() || sending || !thread}>
              {sending ? <LoaderCircle size={14} className="split-pane-spinner" /> : <ArrowUp size={15} strokeWidth={2.1} />}
            </button>
          )}
        </div>
      </form>
    </section>
  );
}

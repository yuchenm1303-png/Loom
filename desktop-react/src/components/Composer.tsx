import { ArrowUp, Paperclip, Sparkles, Square } from "lucide-react";
import {
  type ComponentProps,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useI18n } from "../i18n";
import type { Attachment } from "../types/loom";
import { Composer as ComposerBase } from "./ComposerBase";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";
import {
  MAX_COMPOSER_ATTACHMENTS,
  appendComposerAttachments,
  attachmentFromPath,
  releaseAttachmentPreview,
  resolveComposerFiles,
} from "./composerAttachments";
import { QuoteReplyBar, formatQuotedPrompt, useQuoteReply } from "./quoteReply";
import "./composer.css";


type ComposerProps = ComponentProps<typeof ComposerBase>;

function SteeringComposer({ threadId, onSend, onInterrupt, imagesAllowed = true }: ComposerProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [pendingSends, setPendingSends] = useState(0);
  const [stopping, setStopping] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const [quote, setQuote] = useQuoteReply();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  useEffect(() => {
    if (!quote) return;
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [quote]);

  useEffect(() => {
    setQuote(null);
  }, [threadId, setQuote]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!acknowledged) return;
    const timer = window.setTimeout(() => setAcknowledged(false), 3200);
    return () => window.clearTimeout(timer);
  }, [acknowledged]);

  const addAttachments = (incoming: Attachment[]) => {
    setAttachments((current) => {
      const result = appendComposerAttachments(current, incoming);
      if (result.overflowed) setAttachError(`At most ${MAX_COMPOSER_ATTACHMENTS} attachments per message.`);
      return result.attachments;
    });
  };

  const pickAttachments = async () => {
    const picked = (await window.loom?.pickFiles?.()) ?? [];
    setAttachError("");
    addAttachments(picked.map((filePath) => attachmentFromPath(filePath)));
  };

  const removeAttachment = (id: string) => {
    setAttachments((current) => {
      const target = current.find((item) => item.id === id);
      releaseAttachmentPreview(target);
      return current.filter((item) => item.id !== id);
    });
  };

  const onPaste = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...event.clipboardData.files];
    if (!files.length) return;
    event.preventDefault();
    setAttachError("");
    addAttachments(await resolveComposerFiles(files, setAttachError));
  };

  const onDrop = async (event: React.DragEvent) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    setDragging(false);
    setAttachError("");
    addAttachments(await resolveComposerFiles([...event.dataTransfer.files], setAttachError));
  };

  function submit(event?: FormEvent): void {
    event?.preventDefault();
    const typedInput = value.trim();
    const input = formatQuotedPrompt(quote, typedInput);
    // Steering has two clocks: the conversation should react immediately, while
    // the durable RPC may finish later at a safe boundary. Do not freeze the
    // composer on that network clock.
    const sendable = attachments.filter((item) => imagesAllowed || !item.isImage);
    if ((!typedInput && !quote && !sendable.length) || stopping) return;
    setAcknowledged(false);
    setError("");

    let request: Promise<void>;
    try {
      request = Promise.resolve(onSend(input, sendable.map((item) => ({ path: item.path, name: item.name }))));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return;
    }

    setValue("");
    setQuote(null);
    setAttachments([]);
    setAttachError("");
    setPendingSends((current) => current + 1);
    requestAnimationFrame(() => textareaRef.current?.focus());

    void request
      .then(() => {
        setAcknowledged(true);
      })
      .catch((cause) => {
        setAcknowledged(false);
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        setPendingSends((current) => Math.max(0, current - 1));
      });
  }

  async function stop(): Promise<void> {
    if (stopping) return;
    setStopping(true);
    setAcknowledged(false);
    setError("");
    try {
      // Once interrupt is accepted, the backend cancellation token is terminal
      // for steering this turn. Keep the surface locked until the parent swaps
      // back to the ordinary composer on turn/completed.
      await onInterrupt();
    } catch (cause) {
      setStopping(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && quote) {
      event.preventDefault();
      setQuote(null);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  const activityLabel = stopping
    ? (zh ? "正在停止…" : "Stopping…")
    : pendingSends > 0
      ? (zh ? "补充要求已发送，正在确认…" : "Guidance sent · confirming…")
      : acknowledged
        ? (zh ? "已收到补充要求" : "Guidance received")
        : (zh ? "任务进行中" : "Task in progress");

  return (
    <div className="composer-wrap live-steering-composer">
      <form
        // Live steering is deliberately not `.is-running`: that legacy class
        // belongs to the old non-interactive working footer and historically
        // carried rules that suppressed the input row. Steering is its own
        // editable state and must never inherit those semantics again.
        className={`composer is-steering ${focused ? "is-focused" : ""} ${dragging ? "is-dragging" : ""}`}
        onSubmit={submit}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDragging(false);
        }}
        onDrop={(event) => void onDrop(event)}
      >
        <span className="composer-glow" aria-hidden="true" />

        {quote ? <QuoteReplyBar quote={quote} onClear={() => setQuote(null)} /> : null}
        <ComposerAttachmentStrip attachments={attachments} imagesAllowed={imagesAllowed} onRemove={removeAttachment} />
        {attachError ? <p className="composer-attach-error">{attachError}</p> : null}
        {!imagesAllowed && attachments.some((item) => item.isImage) ? (
          <p className="composer-attach-error">
            This model cannot read images in the active turn. Other files can still be attached.
          </p>
        ) : null}
        {error ? <p className="composer-attach-error">{error}</p> : null}

        <div className="composer-input-row">
          <span className="composer-spark" aria-hidden="true"><Sparkles size={15} /></span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              if (acknowledged) setAcknowledged(false);
            }}
            onKeyDown={onKeyDown}
            onPaste={(event) => void onPaste(event)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={quote ? (zh ? "针对引用内容补充要求…" : "Guide using the quoted context…") : (zh ? "补充要求，调整当前任务…" : "Guide the current task…")}
            aria-label="Guide the current task"
            disabled={stopping}
            rows={1}
          />
        </div>

        <div className="composer-toolbar composer-toolbar-steering">
          <div className="composer-left composer-status-zone">
            <button
              type="button"
              className="composer-tool"
              title={zh ? "给当前任务补充图片或文件" : "Attach files to the active task"}
              onClick={() => void pickAttachments()}
              disabled={stopping}
            >
              <Paperclip size={15} />
              <span>{zh ? "附件" : "Attach"}</span>
            </button>
            <span className="composer-running-label" role="status" aria-live="polite">
              <i aria-hidden="true" />
              <span className="composer-running-copy">{activityLabel}</span>
            </span>
          </div>
          <div className="composer-right">
            <button
              type="submit"
              className="send-button"
              disabled={stopping || (!value.trim() && !quote && !attachments.some((item) => imagesAllowed || !item.isImage))}
              title="Guide current task"
              aria-label="Guide current task"
            >
              <ArrowUp size={17} strokeWidth={2.2} />
            </button>
            <button
              type="button"
              className="send-button stop"
              disabled={stopping}
              onClick={() => void stop()}
              title="Stop current turn"
              aria-label="Stop current turn"
            >
              <Square size={12} fill="currentColor" />
            </button>
          </div>
        </div>
      </form>
      <div className="composer-hint">
        {pendingSends > 0
          ? (zh ? "消息已立即显示，正在后台确认…" : "Shown immediately · confirming in the background…")
          : acknowledged
            ? (zh ? "已确认，Loom 将根据补充要求继续。" : "Confirmed. Loom will continue with your guidance.")
            : (zh ? "可以随时补充要求，或点击停止结束任务。" : "Add guidance anytime, or stop to end this task.")}
      </div>
    </div>
  );
}

export function Composer(props: ComposerProps) {
  if (props.running) return <SteeringComposer {...props} />;
  return <ComposerBase {...props} />;
}

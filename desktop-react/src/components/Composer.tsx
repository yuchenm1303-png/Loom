import { ArrowUp, Sparkles, Square } from "lucide-react";
import {
  type ComponentProps,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useI18n } from "../i18n";
import { Composer as ComposerBase } from "./ComposerBase";
import "./composer.css";


type ComposerProps = ComponentProps<typeof ComposerBase>;

function SteeringComposer({ onSend, onInterrupt }: ComposerProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [pendingSends, setPendingSends] = useState(0);
  const [stopping, setStopping] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!acknowledged) return;
    const timer = window.setTimeout(() => setAcknowledged(false), 3200);
    return () => window.clearTimeout(timer);
  }, [acknowledged]);

  function submit(event?: FormEvent): void {
    event?.preventDefault();
    const input = value.trim();
    // Steering has two clocks: the conversation should react immediately, while
    // the durable RPC may finish later at a safe boundary. Do not freeze the
    // composer on that network clock.
    if (!input || stopping) return;
    setAcknowledged(false);
    setError("");

    let request: Promise<void>;
    try {
      request = Promise.resolve(onSend(input, []));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return;
    }

    setValue("");
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
        className={`composer is-steering ${focused ? "is-focused" : ""}`}
        onSubmit={submit}
      >
        <span className="composer-glow" aria-hidden="true" />

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
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={zh ? "补充要求，调整当前任务…" : "Guide the current task…"}
            aria-label="Guide the current task"
            disabled={stopping}
            rows={1}
          />
        </div>

        <div className="composer-toolbar">
          <div className="composer-left">
            <span className="composer-running-label" role="status" aria-live="polite">
              <i /> {activityLabel}
            </span>
          </div>
          <div className="composer-right">
            <span className="composer-keycap">Enter ↵</span>
            <button
              type="submit"
              className="send-button"
              disabled={stopping || !value.trim()}
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

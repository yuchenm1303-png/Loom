import { ArrowUp, Sparkles, Square } from "lucide-react";
import {
  type ComponentProps,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Composer as ComposerBase } from "./ComposerBase";
import "./composer.css";


type ComposerProps = ComponentProps<typeof ComposerBase>;

function SteeringComposer({ disabled, onSend, onInterrupt }: ComposerProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [sending, setSending] = useState(false);
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

  async function submit(event?: FormEvent): Promise<void> {
    event?.preventDefault();
    const input = value.trim();
    if (!input || disabled || sending || stopping) return;
    setSending(true);
    setAcknowledged(false);
    setError("");
    try {
      await onSend(input, []);
      setValue("");
      setAcknowledged(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  }

  async function stop(): Promise<void> {
    if (stopping) return;
    setStopping(true);
    setAcknowledged(false);
    setError("");
    try {
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
    ? "Stopping…"
    : sending
      ? "Sending guidance…"
      : acknowledged
        ? "Guidance received"
        : "Live steering";

  return (
    <div className="composer-wrap live-steering-composer">
      <form
        className={`composer is-running ${focused ? "is-focused" : ""}`}
        onSubmit={(event) => void submit(event)}
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
            placeholder="Guide the current task…"
            aria-label="Guide the current task"
            disabled={disabled || sending || stopping}
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
              disabled={disabled || sending || stopping || !value.trim()}
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
        {acknowledged
          ? "Received. Loom will apply this guidance at the next safe step without ending the turn."
          : "Guidance stays in this turn and is applied at the next safe step. Stop still ends the turn."}
      </div>
    </div>
  );
}

export function Composer(props: ComposerProps) {
  if (props.running) return <SteeringComposer {...props} />;
  return <ComposerBase {...props} />;
}

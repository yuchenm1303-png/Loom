import { ArrowUp, Paperclip, ShieldCheck, Sparkles, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import "./composer.css";

interface ComposerProps {
  disabled?: boolean;
  running?: boolean;
  model?: string;
  permissionMode?: string;
  onSend(input: string): Promise<void> | void;
  onInterrupt(): Promise<void> | void;
}

export function Composer({ disabled, running, model, permissionMode, onSend, onInterrupt }: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "0px";
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, 48), 180);
    textarea.style.height = `${nextHeight}px`;
  }, [value]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const input = value.trim();
    if (!input || disabled || running) return;
    setValue("");
    await onSend(input);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <div className="composer-wrap composer-wrap-v2">
      <form className="composer composer-v2" onSubmit={submit}>
        <span className="composer-accent-line" aria-hidden="true" />

        <div className="composer-input-row">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={disabled ? "Open a thread to start" : "Ask Loom anything…"}
            aria-label="Message Loom"
            disabled={disabled}
            rows={1}
          />
        </div>

        <div className="composer-toolbar">
          <div className="composer-left">
            <button
              type="button"
              className="composer-tool-button"
              title="Attachments are coming soon"
              aria-label="Attach files"
              disabled
            >
              <Paperclip size={16} strokeWidth={1.8} />
            </button>

            <span className="composer-divider" aria-hidden="true" />

            <span className="composer-chip permission-chip" title="Current permission mode">
              <ShieldCheck size={13} strokeWidth={1.8} />
              {permissionMode || "approval"}
            </span>

            <span className="composer-chip model-chip" title={model || "Current model"}>
              <Sparkles size={13} strokeWidth={1.8} />
              {model || "Model"}
            </span>
          </div>

          {running ? (
            <button
              type="button"
              className="send-button stop"
              onClick={() => void onInterrupt()}
              title="Stop generation"
              aria-label="Stop generation"
            >
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              className="send-button"
              disabled={disabled || !value.trim()}
              title="Send message"
              aria-label="Send message"
            >
              <ArrowUp size={17} strokeWidth={2.1} />
            </button>
          )}
        </div>
      </form>

      <div className="composer-meta" aria-hidden="true">
        {running ? (
          <span className="composer-running-label">
            <span className="composer-running-dot" />
            Loom is working
          </span>
        ) : (
          <>
            <span className="composer-meta-group">
              <span className="composer-key">↵</span>
              send
            </span>
            <span className="composer-meta-dot" />
            <span className="composer-meta-group">
              <span className="composer-key">Shift</span>
              <span className="composer-key">↵</span>
              newline
            </span>
          </>
        )}
      </div>
    </div>
  );
}

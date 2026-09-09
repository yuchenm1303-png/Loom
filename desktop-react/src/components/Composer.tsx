import { ArrowUp, Cpu, Paperclip, ShieldCheck, Sparkles, Square } from "lucide-react";
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
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
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
    <div className="composer-wrap">
      <form className={`composer ${focused ? "is-focused" : ""} ${running ? "is-running" : ""}`} onSubmit={submit}>
        <span className="composer-glow" aria-hidden="true" />
        <div className="composer-input-row">
          <span className="composer-spark" aria-hidden="true"><Sparkles size={15} /></span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={disabled ? "Open a thread to start" : running ? "Loom is working…" : "Ask Loom to inspect, build, debug, or automate…"}
            disabled={disabled || running}
            rows={1}
          />
        </div>

        <div className="composer-toolbar">
          <div className="composer-left">
            <button type="button" className="composer-tool" title="Attach files" disabled>
              <Paperclip size={15} />
              <span>Attach</span>
            </button>
            <span className="composer-divider" />
            <button type="button" className="composer-chip" title="Permission mode">
              <ShieldCheck size={13} />
              <span>{permissionMode || "approval"}</span>
            </button>
            <button type="button" className="composer-chip model-chip" title={model || "Model"}>
              <Cpu size={13} />
              <span>{model || "Model"}</span>
            </button>
          </div>

          <div className="composer-right">
            {!running ? <span className="composer-keycap">Enter ↵</span> : <span className="composer-running-label"><i /> Working</span>}
            {running ? (
              <button type="button" className="send-button stop" onClick={() => void onInterrupt()} title="Stop current turn" aria-label="Stop current turn">
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button type="submit" className="send-button" disabled={disabled || !value.trim()} title="Send" aria-label="Send message">
                <ArrowUp size={17} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </div>
      </form>
      <div className="composer-hint">Loom can use your workspace and connected tools. Review sensitive actions before approving them.</div>
    </div>
  );
}

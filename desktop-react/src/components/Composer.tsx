import { ArrowUp, Paperclip, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useState } from "react";

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
      <form className="composer" onSubmit={submit}>
        <textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={disabled ? "Open a thread to start" : "Ask Loom anything…"}
          disabled={disabled}
          rows={1}
        />
        <div className="composer-toolbar">
          <div className="composer-left">
            <button type="button" className="icon-button" title="Attach files" disabled><Paperclip size={16} /></button>
            <button type="button" className="composer-chip">{permissionMode || "approval"}</button>
            <button type="button" className="composer-chip model-chip">{model || "Model"}</button>
          </div>
          {running ? (
            <button type="button" className="send-button stop" onClick={() => void onInterrupt()} title="Stop"><Square size={13} fill="currentColor" /></button>
          ) : (
            <button type="submit" className="send-button" disabled={disabled || !value.trim()} title="Send"><ArrowUp size={17} /></button>
          )}
        </div>
      </form>
      <div className="composer-hint">Enter to send · Shift+Enter for newline</div>
    </div>
  );
}

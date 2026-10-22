import { useEffect, useRef, useState } from "react";
import { Eye, Maximize2, X } from "./icons";
import { useI18n } from "../i18n";

/** These actions only inspect or resize the draft; neither sends a message. */
export function ComposerDraftTools({ text, attachments, threadId, disabled, onPreviewOpen }: {
  text: string; attachments: { name: string }[]; threadId?: string; disabled?: boolean; onPreviewOpen?(): void;
}) {
  const zh = useI18n().language === "zh-CN";
  const [preview, setPreview] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { setPreview(false); setExpanded(false); }, [threadId]);
  useEffect(() => {
    if (!preview) return;
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setPreview(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setPreview(false); trigger.current?.focus(); }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape); };
  }, [preview]);
  return <div ref={root} className="composer-draft-tools">
    <button type="button" className="composer-tool composer-icon-action composer-editor-toggle" aria-pressed={expanded}
      title={zh ? (expanded ? "收起编辑区" : "展开编辑区") : (expanded ? "Collapse editor" : "Expand editor")}
      aria-label={zh ? (expanded ? "收起编辑区" : "展开编辑区") : (expanded ? "Collapse editor" : "Expand editor")}
      disabled={disabled} onClick={() => setExpanded(value => !value)}><Maximize2 size={14} /></button>
    <button ref={trigger} type="button" className="composer-tool composer-icon-action" title={zh ? "发送前预览" : "Preview message"}
      aria-label={zh ? "发送前预览" : "Preview message"} aria-expanded={preview} aria-haspopup="dialog"
      disabled={disabled || (!text.trim() && !attachments.length)} onClick={() => { if (!preview) onPreviewOpen?.(); setPreview(value => !value); }}><Eye size={15} /></button>
    {preview ? <section className="composer-popover composer-draft-preview" role="dialog" aria-label={zh ? "发送前预览" : "Preview message"}>
      <div className="composer-draft-preview-head"><strong>{zh ? "发送前预览" : "Preview message"}</strong>
        <button type="button" className="composer-tool composer-icon-action" aria-label={zh ? "关闭预览" : "Close preview"}
          onClick={() => { setPreview(false); trigger.current?.focus(); }}><X size={14} /></button></div>
      <div className="composer-draft-preview-body"><p>{text || (zh ? "仅发送附件" : "Attachments only")}</p>
        {attachments.length ? <ul>{attachments.map((item, index) => <li key={index}>{item.name}</li>)}</ul> : null}</div>
      <small>{zh ? "这是待发送的内容，预览不会发送消息。" : "Review your draft. Opening this preview does not send it."}</small>
    </section> : null}
  </div>;
}

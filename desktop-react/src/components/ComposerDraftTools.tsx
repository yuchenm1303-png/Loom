import { useEffect, useLayoutEffect, useState } from "react";
import { Eye, Maximize2, Paperclip } from "./icons";
import { useI18n } from "../i18n";
import { ComposerPanel, PanelFoot, PanelHead, useComposerPanel } from "./composerPanel";

/** These actions only inspect or resize the draft; neither sends a message. The preview is one of the composer's panels. */
export function ComposerDraftTools({ text, attachments, threadId, disabled }: {
  text: string; attachments: { name: string }[]; threadId?: string; disabled?: boolean;
}) {
  const zh = useI18n().language === "zh-CN";
  const panel = useComposerPanel("preview");
  const { close } = panel;
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { close(); setExpanded(false); }, [threadId, close]);
  // The composer grows its field while the editor is expanded. An attribute on the composer, because
  // `.composer:has(.composer-editor-toggle[aria-pressed="true"]) textarea` restyled the field on every change inside it.
  const { anchorRef } = panel;
  useLayoutEffect(() => {
    const composer = anchorRef.current?.closest(".composer");
    if (!composer || !expanded) return;
    composer.setAttribute("data-editor-expanded", "");
    return () => composer.removeAttribute("data-editor-expanded");
  }, [anchorRef, expanded]);
  const label = zh ? "发送前预览" : "Preview message";
  return <div ref={panel.anchorRef} className="composer-draft-tools cp-anchor">
    <button type="button" className="composer-tool composer-icon-action composer-editor-toggle" aria-pressed={expanded}
      title={zh ? (expanded ? "收起编辑区" : "展开编辑区") : (expanded ? "Collapse editor" : "Expand editor")}
      aria-label={zh ? (expanded ? "收起编辑区" : "展开编辑区") : (expanded ? "Collapse editor" : "Expand editor")}
      disabled={disabled} onClick={() => setExpanded(value => !value)}><Maximize2 size={14} /></button>
    <button ref={panel.triggerRef} type="button" className="composer-tool composer-icon-action" title={label}
      aria-label={label} aria-expanded={panel.open} aria-haspopup="dialog"
      disabled={disabled || (!text.trim() && !attachments.length)} onClick={panel.toggle}><Eye size={15} /></button>
    <ComposerPanel panel={panel} label={label} size="l" align="end" className="composer-draft-preview">
      <PanelHead title={zh ? "预览" : "Preview"} aside={zh ? "尚未发送" : "Not sent"} />
      <div className="cp-body">
        <div className={`cp-preview-text${text ? "" : " is-empty"}`}>{text || (zh ? "仅发送附件" : "Attachments only")}</div>
        {attachments.length ? <ul className="cp-files">{attachments.map((item, index) => <li key={index} title={item.name}>
          <Paperclip size={12} strokeWidth={1.75} aria-hidden="true" /><span>{item.name}</span></li>)}</ul> : null}
      </div>
      <PanelFoot>{zh ? "这是待发送的内容，打开预览不会发送消息。" : "Review your draft. Opening this preview does not send it."}</PanelFoot>
    </ComposerPanel>
  </div>;
}

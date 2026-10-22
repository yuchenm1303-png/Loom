import { forwardRef, memo, useImperativeHandle, useRef } from "react";
import { PanelLeft, Search } from "./icons";
import { NewConversationRow, type NewConversationRowHandle } from "./NewConversationRow";
import { useI18n } from "../i18n";
import "./sidebar-controls.css";

const COPY = {
  en: { hide: "Hide sidebar", show: "Show sidebar", search: "Search conversations", compose: "New conversation" },
  zh: { hide: "收起侧栏", show: "展开侧栏", search: "搜索对话", compose: "新对话" },
} as const;

export interface SidebarControlsHandle {
  /** The quill's stroke, for a conversation started from the keyboard. */
  write(): void;
}

interface SidebarControlsProps {
  open: boolean;
  searchOpen: boolean;
  onToggle(): void;
  onSearch(): void;
  onNew(): Promise<void> | void;
}

/**
 * The sidebar's toggle, search and quill: one toolbar on the window's corner that never leaves it. With the sidebar
 * open it lies on the card's top row, the toggle at the row's start and the other two at its end; closed, the card
 * goes and the two glide in beside the toggle, onto the start of the header, which keeps that room free. The same
 * three buttons in both places, so nothing disappears to reappear somewhere else, a pointer resting on the toggle
 * stays on it, and keyboard focus never has to be handed over.
 */
export const SidebarControls = memo(forwardRef<SidebarControlsHandle, SidebarControlsProps>(function SidebarControls(
  { open, searchOpen, onToggle, onSearch, onNew },
  ref,
) {
  const { language } = useI18n();
  const copy = COPY[language === "zh-CN" ? "zh" : "en"];
  const quill = useRef<NewConversationRowHandle>(null);
  useImperativeHandle(ref, () => ({ write: () => quill.current?.write() }), []);
  const toggleLabel = open ? copy.hide : copy.show;
  return (
    <div className="sidebar-controls" data-sidebar={open ? "open" : "closed"}>
      <button
        type="button"
        className="sidebar-controls-button sidebar-toggle"
        data-sidebar-control="toggle"
        aria-expanded={open}
        onClick={onToggle}
        title={toggleLabel}
        aria-label={toggleLabel}
      >
        <PanelLeft size={17} strokeWidth={1.75} aria-hidden="true" />
      </button>
      <div className="sidebar-controls-trail">
        <button
          type="button"
          className={`sidebar-controls-button compact-search-toggle${searchOpen ? " active" : ""}`}
          data-sidebar-control="search"
          onClick={onSearch}
          title={`${copy.search} · Ctrl K`}
          aria-label={copy.search}
        >
          <Search size={16} strokeWidth={1.85} aria-hidden="true" />
        </button>
        <NewConversationRow
          ref={quill}
          className="sidebar-controls-button sidebar-new-conversation"
          label={copy.compose}
          title={`${copy.compose} · Ctrl N`}
          onNew={onNew}
        />
      </div>
    </div>
  );
}));

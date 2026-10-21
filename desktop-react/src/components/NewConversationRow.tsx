import { forwardRef, useImperativeHandle, useState } from "react";
import { Quill } from "./icons";

export interface NewConversationRowHandle {
  /** Make the quill write itself again: a conversation was just started from somewhere other than this row. */
  write(): void;
}

interface NewConversationRowProps {
  label: string;
  title: string;
  onNew(): Promise<void> | void;
  /** The quill alone, named by `label` for assistive technology (the sidebar's top row, the collapsed header). */
  iconOnly?: boolean;
  className?: string;
}

/**
 * The new-conversation control: the quill in the sidebar's top row, and in the header while the
 * sidebar is closed. Each time a conversation is started from it (a click here, or the shortcut
 * through `write`) its quill writes itself again; icon-motion.css plays that from `data-written`.
 * The value alternates, odd and even, so that a second start replays the animation while the
 * first is still running. The count lives here and not in the sidebar, which would redraw every
 * conversation row for a flourish, and a row that mounts later starts at rest.
 */
export const NewConversationRow = forwardRef<NewConversationRowHandle, NewConversationRowProps>(function NewConversationRow(
  { label, title, onNew, iconOnly = false, className = "sidebar-new-conversation" },
  ref,
) {
  const [starts, setStarts] = useState(0);
  useImperativeHandle(ref, () => ({ write: () => setStarts((count) => count + 1) }), []);
  return (
    <button
      type="button"
      className={className}
      data-written={starts ? (starts % 2 ? "odd" : "even") : undefined}
      onClick={() => {
        setStarts((count) => count + 1);
        void onNew();
      }}
      title={title}
      aria-label={iconOnly ? label : undefined}
    >
      <Quill className="sidebar-action-icon sidebar-compose-icon" size={20} strokeWidth={2.1} aria-hidden="true" />
      {iconOnly ? null : <span>{label}</span>}
    </button>
  );
});

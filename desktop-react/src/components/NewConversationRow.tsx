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
}

/**
 * The primary row of the sidebar. Each time a conversation is started from it (a click here, or
 * the shortcut through `write`) its quill writes itself again; icon-motion.css plays that from
 * `data-written`. The value alternates, odd and even, so that a second start replays the
 * animation while the first is still running. The count lives here and not in the sidebar, which
 * would redraw every conversation row for a flourish, and a row that mounts later starts at rest.
 */
export const NewConversationRow = forwardRef<NewConversationRowHandle, NewConversationRowProps>(function NewConversationRow(
  { label, title, onNew },
  ref,
) {
  const [starts, setStarts] = useState(0);
  useImperativeHandle(ref, () => ({ write: () => setStarts((count) => count + 1) }), []);
  return (
    <button
      type="button"
      className="sidebar-new-conversation"
      data-written={starts ? (starts % 2 ? "odd" : "even") : undefined}
      onClick={() => {
        setStarts((count) => count + 1);
        void onNew();
      }}
      title={title}
    >
      <Quill className="sidebar-action-icon sidebar-compose-icon" size={20} strokeWidth={2.1} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
});

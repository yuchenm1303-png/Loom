import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";
import { Quill } from "./icons";

export interface NewConversationRowHandle {
  /** The stroke, for a conversation started from the keyboard shortcut. */
  write(): void;
}

interface NewConversationRowProps {
  /** Names the quill for assistive technology. */
  label: string;
  title: string;
  onNew(): Promise<void> | void;
  className?: string;
}

/**
 * The new-conversation control: the quill alone. Hovered or focused it leans in to write and pressed it gives toward
 * its nib (both in icon-motion.css, from the host's dials); each new conversation, from a click, Enter or the shortcut,
 * is one stroke: the pen flicks up off the page and settles while its barb is struck again. A stroke that is still
 * running is left to finish rather than restarted from the start. No animation delays onNew.
 */
export const NewConversationRow = forwardRef<NewConversationRowHandle, NewConversationRowProps>(function NewConversationRow(
  { label, title, onNew, className = "sidebar-new-conversation" },
  ref,
) {
  const [strokes, setStrokes] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const stroke = useCallback(() => {
    const running = buttonRef.current?.getAnimations({ subtree: true }).some((animation) => (
      animation instanceof CSSAnimation && /^li-(stroke|strike)-/.test(animation.animationName) && animation.playState === "running"
    ));
    if (!running) setStrokes((count) => count + 1);
  }, []);
  useImperativeHandle(ref, () => ({ write: stroke }), [stroke]);
  return (
    <button
      ref={buttonRef}
      type="button"
      className={className}
      data-sidebar-control="compose"
      data-stroke={strokes ? (strokes % 2 ? "odd" : "even") : undefined}
      onClick={() => {
        stroke();
        void onNew();
      }}
      title={title}
      aria-label={label}
    >
      <Quill className="sidebar-action-icon sidebar-compose-icon" size={20} strokeWidth={2.1} aria-hidden="true" />
    </button>
  );
});

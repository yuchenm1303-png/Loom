import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Quill } from "./icons";

export interface NewConversationRowHandle {
  /** Briefly acknowledge a conversation started through the keyboard shortcut. */
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
 * sidebar is closed. Hover / keyboard focus writes once after a short dwell. Pressing never
 * restarts that stroke: it finishes from its current position while the glyph gives toward
 * its nib. The shortcut gets a separate short confirmation. No animation delays onNew.
 */
export const NewConversationRow = forwardRef<NewConversationRowHandle, NewConversationRowProps>(function NewConversationRow(
  { label, title, onNew, iconOnly = false, className = "sidebar-new-conversation" },
  ref,
) {
  const [starts, setStarts] = useState(0);
  const [confirmations, setConfirmations] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const dwell = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const engagement = useRef({ pointer: false, focus: false, played: false });
  const clearDwell = useCallback(() => {
    clearTimeout(dwell.current);
    dwell.current = undefined;
  }, []);
  useEffect(() => clearDwell, [clearDwell]);

  const finishStroke = useCallback(() => {
    clearDwell();
    // Keep the current position and finish the existing gesture in at most ~180ms.
    // updatePlaybackRate preserves currentTime, unlike replacing the animation name.
    for (const animation of buttonRef.current?.getAnimations({ subtree: true }) ?? []) {
      if (animation instanceof CSSAnimation && /^li-(write|flick)-/.test(animation.animationName)) {
        animation.updatePlaybackRate(3);
      }
    }
  }, [clearDwell]);

  const engage = (source: "pointer" | "focus", active: boolean) => {
    const state = engagement.current;
    state[source] = active;
    if (!state.pointer && !state.focus) {
      state.played = false;
      finishStroke();
    } else if (!state.played && dwell.current === undefined) {
      const settling = (buttonRef.current?.getAnimations({ subtree: true }) ?? [])
        .filter((animation) => animation instanceof CSSAnimation && /^li-(write|flick)-/.test(animation.animationName))
        .reduce((remaining, animation) => Math.max(remaining,
          (Number(animation.effect?.getComputedTiming().endTime ?? 0) - Number(animation.currentTime ?? 0)) / animation.playbackRate + 16), 0);
      dwell.current = setTimeout(() => {
        dwell.current = undefined;
        state.played = true;
        setStarts((count) => count + 1);
      }, Math.max(100, settling));
    }
  };
  const confirm = useCallback(() => {
    engagement.current.played = engagement.current.pointer || engagement.current.focus;
    finishStroke();
  }, [finishStroke]);
  useImperativeHandle(ref, () => ({ write: () => {
    confirm();
    setConfirmations((count) => count + 1);
  } }), [confirm]);
  return (
    <button
      ref={buttonRef}
      type="button"
      className={className}
      data-written={starts ? (starts % 2 ? "odd" : "even") : undefined}
      data-confirmed={confirmations ? (confirmations % 2 ? "odd" : "even") : undefined}
      onPointerEnter={(event) => { if (event.pointerType !== "touch") engage("pointer", true); }}
      onPointerLeave={() => engage("pointer", false)}
      onPointerCancel={() => engage("pointer", false)}
      onFocus={(event) => { if (event.currentTarget.matches(":focus-visible")) engage("focus", true); }}
      onBlur={() => engage("focus", false)}
      onPointerDown={confirm}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") confirm(); }}
      onClick={() => {
        confirm();
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

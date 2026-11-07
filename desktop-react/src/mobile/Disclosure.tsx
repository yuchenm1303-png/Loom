import { useId, useState, type ReactNode, type RefObject } from "react";
import { ChevronDown } from "../components/icons";

/**
 * A row that opens to show more, for the phone. It replaces <details>, whose content
 * appears and vanishes in one frame. The room opens with a CSS grid-row transition (so a
 * second tap mid-way simply turns it around), the words fade in after the room starts
 * opening and fade out before it closes, and nothing that carries words moves.
 * The body mounts on the first open and stays, so closing has something to fold away.
 */
export function Disclosure({
  title,
  aside,
  open: controlled,
  onOpenChange,
  className,
  anchor,
  children,
}: {
  title: ReactNode;
  aside?: ReactNode;
  open?: boolean;
  onOpenChange?(open: boolean): void;
  className?: string;
  anchor?: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(false);
  const open = controlled ?? own;
  const [seen, setSeen] = useState(open);
  if (open && !seen) setSeen(true);
  const id = useId();
  const toggle = () => {
    const next = !open;
    if (controlled === undefined) setOwn(next);
    onOpenChange?.(next);
  };
  return (
    <div ref={anchor} className={`m-disclosure${className ? ` ${className}` : ""}`} data-open={open ? "" : undefined}>
      <button type="button" className="m-disclosure-head" aria-expanded={open} aria-controls={id} onClick={toggle}>
        <span className="m-disclosure-title">{title}</span>
        {aside}
        <ChevronDown className="m-disclosure-chevron" data-ic-clock="fold" size={18} strokeWidth={1.75} aria-hidden="true" />
      </button>
      <div className="m-disclosure-body" id={id} role="region" aria-hidden={!open} inert={!open}>
        <div>
          <div className="m-disclosure-inner">{seen ? children : null}</div>
        </div>
      </div>
    </div>
  );
}

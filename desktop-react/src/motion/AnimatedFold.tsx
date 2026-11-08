import { useRef, type ReactNode } from "react";
import { useMotionPresence } from "./useMotionPresence";
import { RETAIN } from "./tokens";
import "./animated-fold.css";

/** One reversible height transition. Closed content is inert immediately and
 * retained only for its exit; reopening uses the same node and current height. */
export function AnimatedFold({ open, children, className = "", fade = true }: {
  open: boolean; children: ReactNode; className?: string; fade?: boolean;
}) {
  const presence = useMotionPresence(open, RETAIN.fold);
  const cached = useRef(children);
  if (open) cached.current = children;
  if (!presence.mounted) return null;
  return <div className={`loom-fold ${className}`.trim()} data-open={open} data-fade={fade} aria-hidden={!open} inert={!open}>
    <div className="loom-fold-clip"><div className="loom-fold-content">{open ? children : cached.current}</div></div>
  </div>;
}

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { useMotionPresence } from "./useMotionPresence";
import { RETAIN } from "./tokens";
import "./animated-fold.css";

/** One reversible height transition. Closed content is inert immediately and
 * retained only for its exit; reopening uses the same node and current height. */
export function AnimatedFold({ open, children, className = "", fade = true, appear = true }: {
  open: boolean; children: ReactNode; className?: string; fade?: boolean;
  /** False when what is open from the start is simply there; it still folds once it has closed. */
  appear?: boolean;
}) {
  const presence = useMotionPresence(open, RETAIN.fold);
  const cached = useRef(children);
  if (open) cached.current = children;
  const settled = useRef(!appear && open);
  if (!open) settled.current = false;
  const element = useRef<HTMLDivElement>(null);
  // A WebView older than @starting-style would otherwise pop open: its entrance is played through by hand.
  useLayoutEffect(() => {
    const fold = element.current;
    if (!fold || !presence.mounted || settled.current || "CSSStartingStyleRule" in window) return;
    fold.setAttribute("data-from", "");
    void fold.offsetHeight;
    fold.removeAttribute("data-from");
  }, [presence.mounted]);
  if (!presence.mounted) return null;
  return <div ref={element} className={`loom-fold ${className}`.trim()} data-open={open} data-fade={fade} data-appear={!settled.current} aria-hidden={!open} inert={!open}>
    <div className="loom-fold-clip"><div className="loom-fold-content">{open ? children : cached.current}</div></div>
  </div>;
}

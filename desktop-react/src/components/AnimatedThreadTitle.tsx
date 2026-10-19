import { useLayoutEffect, useRef } from "react";
import { DUR, EASE } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";
import "./animated-thread-title.css";

/** Words fade, they do not travel: a changed title comes in on opacity alone. Nothing replays when a row is mounted. */
export function AnimatedThreadTitle({ title }: { title: string }) {
  const element = useRef<HTMLSpanElement>(null);
  const previous = useRef(title);

  useLayoutEffect(() => {
    const changed = previous.current !== title;
    previous.current = title;
    if (!changed || !element.current || prefersReducedMotionNow()) return;
    const animation = element.current.animate(
      [{ opacity: 0 }, { opacity: 1 }],
      { duration: DUR.fast, easing: EASE.out },
    );
    return () => animation.cancel();
  }, [title]);

  return <span ref={element} className="animated-thread-title">{title}</span>;
}

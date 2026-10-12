import { useLayoutEffect, useRef } from "react";
import "./animated-thread-title.css";

/** Animate title changes without replaying when an existing row is mounted. */
export function AnimatedThreadTitle({ title }: { title: string }) {
  const element = useRef<HTMLSpanElement>(null);
  const previous = useRef(title);

  useLayoutEffect(() => {
    const changed = previous.current !== title;
    previous.current = title;
    if (!changed || !element.current || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const animation = element.current.animate([
      { opacity: 0, transform: "translateY(4px)", filter: "blur(2px)" },
      { opacity: 1, transform: "translateY(0)", filter: "blur(0)" },
    ], { duration: 320, easing: "cubic-bezier(.22, 1, .36, 1)" });
    return () => animation.cancel();
  }, [title]);

  return <span ref={element} className="animated-thread-title">{title}</span>;
}

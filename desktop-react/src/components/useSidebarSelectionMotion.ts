import { useLayoutEffect, useRef, type RefObject } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";

/** Move only the selection surface; titles and controls keep their geometry. */
export function useSidebarSelectionMotion(scrollRef: RefObject<HTMLDivElement | null>, activeId?: string) {
  const reduce = useReducedMotion();
  const previous = useRef<{ id?: string; pip: HTMLElement | null }>({ pip: null });
  const animations = useRef<Animation[]>([]);
  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    const pip = scroller?.querySelector<HTMLElement>(".compact-thread-row.active .thread-selection-pill") ?? null;
    const old = previous.current;
    if (old.id === activeId && old.pip === pip && !reduce) return;
    const from = old.pip?.isConnected ? old.pip.getBoundingClientRect() : null;
    animations.current.forEach(animation => animation.cancel());
    animations.current = [];
    previous.current = { id: activeId, pip };
    if (!scroller || !pip || !from || !old.pip || reduce || old.id === activeId || document.hidden) return;
    // Filtering, collapsed projects and off-screen selections start in place.
    // Their highlight must never fly through unrelated groups or scroll into view.
    if (old.pip.closest('.project-thread-list-shell:not(.open)')
      || pip.closest('.project-thread-list-shell:not(.open)')) return;
    const to = pip.getBoundingClientRect();
    const viewport = scroller.getBoundingClientRect();
    if (from.top < viewport.top || from.bottom > viewport.bottom
      || to.top < viewport.top || to.bottom > viewport.bottom) return;
    const dx = from.left - to.left;
    const dy = from.top - to.top;
    const distance = Math.hypot(dx, dy);
    if (distance < 1) return;
    const duration = Math.min(420, 240 + distance * .3);
    animations.current = [pip.animate(
      { translate: [`${dx}px ${dy}px`, "0px 0px"] },
      { duration, easing: "cubic-bezier(.2,0,0,1)" },
    ), pip.animate(
      { scale: [`${from.width / to.width} ${from.height / to.height}`, `1 ${1 + Math.min(.15, distance / 1200)}`, "1 1"], offset: [0, .35, 1] },
      { duration, easing: "ease-in-out" },
    )];
  });
  useLayoutEffect(() => () => {
    animations.current.forEach(animation => animation.cancel());
  }, []);
}

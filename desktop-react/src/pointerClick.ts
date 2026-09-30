import "./pointer-click.css";

const ripples = new Map<HTMLElement, Animation>();
let layer: HTMLDivElement | null = null;

function clear() {
  for (const [element, animation] of ripples) {
    animation.cancel();
    element.remove();
  }
  ripples.clear();
}

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0 || !event.isPrimary || event.pointerType === "touch"
    || document.hidden || document.documentElement.dataset.loomReducedMotion === "true"
    || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

  if (!layer) {
    layer = document.createElement("div");
    layer.className = "loom-pointer-click-layer";
    layer.setAttribute("aria-hidden", "true");
    document.body.append(layer);
  }
  // Rapid clicking cannot accumulate an unbounded animated DOM.
  if (ripples.size >= 8) {
    const oldest = ripples.keys().next().value as HTMLElement;
    ripples.get(oldest)?.cancel();
    ripples.delete(oldest);
    oldest.remove();
  }
  const ripple = document.createElement("span");
  ripple.className = "loom-pointer-click-ripple";
  ripple.style.left = `${event.clientX}px`;
  ripple.style.top = `${event.clientY}px`;
  layer.append(ripple);
  const animation = ripple.animate([
    { transform: "translate(-50%, -50%) scale(.25)", opacity: .7 },
    { transform: "translate(-50%, -50%) scale(1)", opacity: 0 },
  ], { duration: 420, easing: "cubic-bezier(.16, 1, .3, 1)" });
  ripples.set(ripple, animation);
  const remove = () => { ripples.delete(ripple); ripple.remove(); };
  void animation.finished.then(remove, remove);
}

const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
const observer = new MutationObserver(() => {
  if (document.documentElement.dataset.loomReducedMotion === "true") clear();
});
observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-reduced-motion"] });
media?.addEventListener("change", clear);
document.addEventListener("pointerdown", onPointerDown, { capture: true, passive: true });
document.addEventListener("visibilitychange", clear);
window.addEventListener("blur", clear);

if (import.meta.hot) import.meta.hot.dispose(() => {
  clear();
  layer?.remove();
  observer.disconnect();
  media?.removeEventListener("change", clear);
  document.removeEventListener("pointerdown", onPointerDown, true);
  document.removeEventListener("visibilitychange", clear);
  window.removeEventListener("blur", clear);
});

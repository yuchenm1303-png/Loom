// Pointer light for the work log. While the pointer is over an expandable row
// (or a folded stage's line), the row-relative pointer position is written
// to --lens-x / --lens-y so weave.css can centre a soft radial
// light there. Only two custom properties change; nothing moves. Modelled on
// starterCardPointerGlow.ts: rAF-throttled, cleared when the pointer leaves,
// and off entirely under reduced motion.
//
// The empty export makes this a module: without it, its top-level `let`s would
// share the global scope with starterCardPointerGlow.ts and collide.
export {};

const CAPSULE_SELECTOR = ".wv-row.is-expandable, .wv-line";

let active: HTMLElement | null = null;
let pointerX = 0;
let pointerY = 0;
let frame = 0;

function capsuleFromTarget(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>(CAPSULE_SELECTOR) : null;
}

function release(capsule: HTMLElement | null) {
  if (!capsule) return;
  capsule.style.removeProperty("--lens-x");
  capsule.style.removeProperty("--lens-y");
}

function flush() {
  frame = 0;
  const capsule = active;
  if (!capsule || !capsule.isConnected) return;

  const rect = capsule.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  capsule.style.setProperty("--lens-x", `${(pointerX - rect.left).toFixed(1)}px`);
  capsule.style.setProperty("--lens-y", `${(pointerY - rect.top).toFixed(1)}px`);
}

function leave() {
  if (frame) {
    window.cancelAnimationFrame(frame);
    frame = 0;
  }
  release(active);
  active = null;
}

document.addEventListener("pointermove", (event) => {
  const next = capsuleFromTarget(event.target);
  if (next !== active) {
    release(active);
    active = next;
  }
  if (!active) return;
  if (document.documentElement.dataset.loomReducedMotion === "true") {
    leave();
    return;
  }

  pointerX = event.clientX;
  pointerY = event.clientY;
  if (!frame) frame = window.requestAnimationFrame(flush);
}, { passive: true });

document.addEventListener("pointerout", (event) => {
  const from = capsuleFromTarget(event.target);
  if (!from || from !== active) return;
  if (capsuleFromTarget(event.relatedTarget) === from) return;
  leave();
}, { passive: true });

window.addEventListener("blur", leave);

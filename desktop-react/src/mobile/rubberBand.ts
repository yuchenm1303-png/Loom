/**
 * Edge give for the phone's scroll areas.
 *
 * Android's WebView draws an edge effect only for the page itself; the scrolling boxes inside it (the lists, the
 * conversation, a sheet) simply stop at their ends, which feels like hitting a wall. This gives them the two things a
 * finger expects at an end:
 *
 *  - a drag past the end moves the box with it, harder and harder to pull (an asymptote, not a wall), and it springs
 *    home on release;
 *  - a fling that arrives at the end carries a little way past it, in proportion to its speed, and settles back.
 *
 * Only the box's own transform moves (a compositor property); its scroll position and content are untouched, so the
 * conversation's follow-the-bottom logic never sees a thing. All listeners are passive: nothing here can delay a scroll.
 */
const SCROLLERS = ".m-body, .transcript-scroll, .m-build-stage, .m-sheet";
/** Spring toward rest: a little under critical damping, so a fling returns with one soft overshoot, not a wobble. */
const STIFFNESS = 190;
const DAMPING_RATIO = 0.8;
/** The pull's reach: the most a finger can move the box, as a share of its height (and a ceiling in px). */
const REACH_SHARE = 0.3;
const REACH_MAX = 190;
/** How fast the box follows the finger at the start of a pull (1 = the finger's own speed). */
const PULL_GAIN = 0.6;
/** A fling needs this speed (px/ms) at the end to carry past it; the carry is this share of it, capped (px/s). */
const FLING_MIN = 0.7;
const FLING_CARRY = 0.3;
const FLING_CAP = 1200;
/** Only a scroll that follows the user's own touch can bounce, not the conversation following new text. */
const FLING_WINDOW_MS = 1500;

type Edge = "top" | "bottom";
interface Pull { el: HTMLElement; edge: Edge; raw: number }

const reduced = () => document.documentElement.dataset.loomReducedMotion === "true"
  || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/** The screens themselves give at an end even when their content fits; a sheet only when it really scrolls. */
const PAGES = ".m-body, .transcript-scroll, .m-build-stage";
const scrollerOf = (target: EventTarget | null): HTMLElement | null => {
  const el = target instanceof Element ? target.closest<HTMLElement>(SCROLLERS) : null;
  return el && (el.scrollHeight > el.clientHeight + 1 || el.matches(PAGES)) ? el : null;
};
const reach = (el: HTMLElement) => Math.min(REACH_MAX, el.clientHeight * REACH_SHARE);
/** Finger travel past the end to how far the box moves: slope PULL_GAIN at first, never beyond the reach. */
const offsetOf = (raw: number, el: HTMLElement) => {
  const range = reach(el);
  return range * (1 - 1 / (raw * PULL_GAIN / range + 1));
};
const rawOf = (offset: number, el: HTMLElement) => {
  const range = reach(el);
  return (range / PULL_GAIN) * (1 / (1 - Math.min(offset, range * 0.98) / range) - 1);
};

export function installRubberBand(): () => void {
  let pull: Pull | null = null;
  let lastY = 0;
  let touching = false;
  let lastTouchEnd = 0;
  let spring: { el: HTMLElement; frame: number } | null = null;
  const lastScroll = new WeakMap<HTMLElement, { top: number; at: number }>();

  const place = (el: HTMLElement, offset: number) => { el.style.transform = offset ? `translate3d(0, ${offset.toFixed(2)}px, 0)` : ""; };
  const offsetNow = (el: HTMLElement) => {
    const match = /translate3d\(0(?:px)?, (-?[\d.]+)px/.exec(el.style.transform);
    return match ? Number(match[1]) : 0;
  };
  const stopSpring = () => { if (spring) { cancelAnimationFrame(spring.frame); spring = null; } };

  const release = (el: HTMLElement, from: number, velocity: number) => {
    stopSpring();
    const omega = Math.sqrt(STIFFNESS);
    const damping = 2 * DAMPING_RATIO * omega;
    let x = from, v = velocity, at = performance.now();
    const state = { el, frame: 0 };
    const step = (now: number) => {
      const dt = Math.min(0.034, (now - at) / 1000); at = now;
      v += (-STIFFNESS * x - damping * v) * dt;
      x += v * dt;
      if (Math.abs(x) < 0.25 && Math.abs(v) < 6) { place(el, 0); el.style.overflowY = ""; spring = null; return; }
      place(el, x);
      state.frame = requestAnimationFrame(step);
      spring = state;
    };
    state.frame = requestAnimationFrame(step);
    spring = state;
  };

  const onStart = (event: TouchEvent) => {
    touching = true;
    lastY = event.touches[0]?.clientY ?? 0;
    pull = null;
    if (reduced()) return;
    const el = scrollerOf(event.target);
    if (!el) return;
    // A finger landing on a box that is still springing home takes hold of it where it is.
    const holding = spring?.el === el ? offsetNow(el) : 0;
    stopSpring();
    if (holding) {
      const edge: Edge = holding > 0 ? "top" : "bottom";
      pull = { el, edge, raw: edge === "top" ? rawOf(holding, el) : -rawOf(-holding, el) };
      el.style.overflowY = "hidden";
    } else {
      pull = { el, edge: "top", raw: 0 };
    }
  };

  const onMove = (event: TouchEvent) => {
    const y = event.touches[0]?.clientY ?? lastY;
    const dy = y - lastY;
    lastY = y;
    if (!pull || reduced()) return;
    const { el } = pull;
    if (pull.raw === 0) {
      const atTop = el.scrollTop <= 0;
      const atBottom = el.scrollTop >= el.scrollHeight - el.clientHeight - 1;
      if (atTop && dy > 0) pull.edge = "top";
      else if (atBottom && dy < 0) pull.edge = "bottom";
      else return;
      // The box's own scrolling pauses while it is pulled, so reversing does not also scroll the list.
      el.style.overflowY = "hidden";
    }
    pull.raw += dy;
    if ((pull.edge === "top" && pull.raw <= 0) || (pull.edge === "bottom" && pull.raw >= 0)) {
      pull.raw = 0;
      place(el, 0);
      el.style.overflowY = "";
      return;
    }
    const offset = offsetOf(Math.abs(pull.raw), el);
    place(el, pull.edge === "top" ? offset : -offset);
  };

  const onEnd = () => {
    touching = false;
    lastTouchEnd = performance.now();
    if (!pull) return;
    const { el, raw } = pull;
    pull = null;
    if (raw === 0) { el.style.overflowY = ""; return; }
    release(el, offsetNow(el), 0);
  };

  const onScroll = (event: Event) => {
    const el = event.target instanceof HTMLElement && event.target.matches(SCROLLERS) ? event.target : null;
    if (!el) return;
    const now = performance.now();
    const before = lastScroll.get(el);
    lastScroll.set(el, { top: el.scrollTop, at: now });
    if (!before || touching || reduced() || now - lastTouchEnd > FLING_WINDOW_MS || spring?.el === el) return;
    const dt = now - before.at;
    if (dt <= 0 || dt > 80) return;
    const speed = (el.scrollTop - before.top) / dt;
    const max = el.scrollHeight - el.clientHeight;
    const carry = Math.min(FLING_CAP, Math.abs(speed) * 1000 * FLING_CARRY);
    if (speed < -FLING_MIN && el.scrollTop <= 0) release(el, 0, carry);
    else if (speed > FLING_MIN && el.scrollTop >= max - 1) release(el, 0, -carry);
  };

  document.addEventListener("touchstart", onStart, { capture: true, passive: true });
  document.addEventListener("touchmove", onMove, { capture: true, passive: true });
  document.addEventListener("touchend", onEnd, { capture: true, passive: true });
  document.addEventListener("touchcancel", onEnd, { capture: true, passive: true });
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
  return () => {
    stopSpring();
    document.removeEventListener("touchstart", onStart, true);
    document.removeEventListener("touchmove", onMove, true);
    document.removeEventListener("touchend", onEnd, true);
    document.removeEventListener("touchcancel", onEnd, true);
    document.removeEventListener("scroll", onScroll, true);
  };
}

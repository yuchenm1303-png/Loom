/**
 * The phone's motion, where it needs a script (mobile-motion.css has the rest).
 *
 * Everything here works on an old WebView: entrances do not rely on @starting-style and the springs are sampled into
 * keyframes instead of relying on the CSS linear() curve, because the phone's WebView can be years behind Chrome.
 */
import { useEffect, type RefObject } from "react";
import { DUR } from "../motion/tokens";

/**
 * Plays a surface's entrance: it is put in its `[data-from]` state and released in one go, so its transition runs from
 * there on the very frame the surface first appears (and turns around from where it is, if it is closed again).
 */
export function playEntrance(...elements: ReadonlyArray<HTMLElement | null | undefined>): void {
  const present = elements.filter((element): element is HTMLElement => Boolean(element));
  if (!present.length) return;
  for (const element of present) element.setAttribute("data-from", "");
  void present[0].offsetHeight;
  for (const element of present) element.removeAttribute("data-from");
}

/** How a selection capsule travels, in the capsule's own terms. */
export type CapsuleSample = { x: number; speed: number; scaleX: number; scaleY: number };
export type CapsuleTrack = { duration: number; samples: CapsuleSample[]; at(ms: number): { x: number; velocity: number } };

// Damping .72 at this stiffness: most of the way in a quarter of a second, about 4% past the mark, then still.
const CAPSULE_OMEGA = 17 / 1000;
const CAPSULE_ZETA = 0.72;
const CAPSULE_SAMPLES = 40;
const CAPSULE_DURATION = DUR.slow;
/** How much longer a capsule gets at speed, in px per (px/ms), and the most it may gain. */
const STRETCH_PER_SPEED = 14;
const STRETCH_MAX = 22;
/** The height it gives up for the length it gains: a drop in motion keeps roughly its area. */
const SQUEEZE = 0.5;

/**
 * One damped spring from `from` to `to`, starting at `velocity` (px/ms), with the capsule's stretch read off its own
 * speed: it is longest where it is fastest and exactly its own shape when it stops, on the same clock as its travel.
 */
export function capsuleTrack(from: number, to: number, width: number, velocity = 0): CapsuleTrack {
  const span = to - from;
  const damped = CAPSULE_OMEGA * Math.sqrt(1 - CAPSULE_ZETA * CAPSULE_ZETA);
  const decay = CAPSULE_ZETA * CAPSULE_OMEGA;
  // In terms of the distance still to go: u(0) = -span, u'(0) = velocity.
  const a = -span;
  const b = (velocity + decay * a) / damped;
  const at = (ms: number) => {
    const envelope = Math.exp(-decay * ms);
    const c = Math.cos(damped * ms);
    const s = Math.sin(damped * ms);
    return {
      x: to + envelope * (a * c + b * s),
      velocity: envelope * ((b * damped - a * decay) * c - (a * damped + b * decay) * s),
    };
  };
  const samples: CapsuleSample[] = [];
  for (let index = 0; index <= CAPSULE_SAMPLES; index += 1) {
    const point = at((index / CAPSULE_SAMPLES) * CAPSULE_DURATION);
    const speed = Math.abs(point.velocity);
    const gain = index === CAPSULE_SAMPLES ? 0 : Math.min(STRETCH_MAX, speed * STRETCH_PER_SPEED);
    samples.push({
      x: index === CAPSULE_SAMPLES ? to : point.x,
      speed,
      scaleX: 1 + gain / Math.max(width, 1),
      scaleY: 1 - (gain / Math.max(width, 1)) * SQUEEZE,
    });
  }
  return { duration: CAPSULE_DURATION, samples, at };
}

export type CapsuleRun = { track: CapsuleTrack; carrier: Animation; pill: Animation; startedAt: number };

/** The painted x of an element positioned by a translate. */
export function translatedX(element: Element): number {
  const transform = getComputedStyle(element).transform;
  return transform === "none" ? 0 : new DOMMatrixReadOnly(transform).m41;
}

/**
 * Sends a capsule to `to`. `carrier` holds the place and `pill` the shape; the two animations share a clock, so the
 * stretch can never outlast the travel. A capsule caught in flight keeps its speed. Width changes ride the same track.
 */
export function runCapsule(options: {
  carrier: HTMLElement;
  pill: HTMLElement;
  from: number;
  to: number;
  width: number;
  fromWidth?: number;
  previous: CapsuleRun | null;
}): CapsuleRun {
  const { carrier, pill, from, to, width, fromWidth = width, previous } = options;
  const velocity = previous ? previous.track.at(performance.now() - previous.startedAt).velocity : 0;
  previous?.carrier.cancel();
  previous?.pill.cancel();
  const track = capsuleTrack(from, to, width, velocity);
  const count = track.samples.length - 1;
  const carrierFrames = track.samples.map((sample, index) => {
    const frame: Keyframe = { transform: `translateX(${sample.x.toFixed(2)}px)`, offset: index / count };
    if (fromWidth !== width) frame.width = `${(fromWidth + (width - fromWidth) * Math.min(1, (sample.x - from) / ((to - from) || 1))).toFixed(2)}px`;
    return frame;
  });
  const pillFrames = track.samples.map((sample, index) => ({ transform: `scale(${sample.scaleX.toFixed(4)}, ${sample.scaleY.toFixed(4)})`, offset: index / count }));
  const timing = { duration: track.duration, easing: "linear" };
  return { track, carrier: carrier.animate(carrierFrames, timing), pill: pill.animate(pillFrames, timing), startedAt: performance.now() };
}

/** What counts as a control for a finger. */
const PRESSABLE = 'button, summary, a[href], [role="button"], [role="radio"], [role="tab"], [role="option"], [role="menuitem"]';
/** A touch that may yet become a scroll waits this long before it shows. */
const PRESS_DELAY_MS = 40;
/** A quick tap still has to be seen. */
const PRESS_HOLD_MS = DUR.press;
const SLOP_PX = 10;

/**
 * Press feedback for a finger. The browser holds `:active` back on touch until it knows the touch is not a scroll, so
 * a quick tap showed nothing at all. This marks the control itself: soon after the finger lands, at once when it lifts
 * (and then long enough to be seen), and not at all if the touch turns into a scroll.
 */
export function installTouchPress(root: Document = document): () => void {
  let control: HTMLElement | null = null;
  let timer = 0;
  let shownAt = 0;
  let startX = 0;
  let startY = 0;
  const show = (element: HTMLElement) => {
    window.clearTimeout(timer);
    timer = 0;
    shownAt = performance.now();
    element.setAttribute("data-pressed", "");
    if (!element.hasAttribute("data-ic-force")) element.setAttribute("data-ic-force", "down");
  };
  const hide = (element: HTMLElement) => {
    element.removeAttribute("data-pressed");
    if (element.getAttribute("data-ic-force") === "down") element.removeAttribute("data-ic-force");
  };
  const release = (lift: boolean) => {
    const element = control;
    if (!element) return;
    control = null;
    const waiting = timer !== 0;
    window.clearTimeout(timer);
    timer = 0;
    if (!lift) { hide(element); return; }
    if (waiting) show(element);
    window.setTimeout(() => hide(element), Math.max(0, PRESS_HOLD_MS - (performance.now() - shownAt)));
  };
  const down = (event: PointerEvent) => {
    release(false);
    const element = (event.target as Element | null)?.closest<HTMLElement>(PRESSABLE);
    if (!element || (element as HTMLButtonElement).disabled || element.getAttribute("aria-disabled") === "true") return;
    control = element;
    startX = event.clientX;
    startY = event.clientY;
    timer = window.setTimeout(() => show(element), PRESS_DELAY_MS);
  };
  const move = (event: PointerEvent) => {
    if (control && Math.hypot(event.clientX - startX, event.clientY - startY) > SLOP_PX) release(false);
  };
  const up = () => release(true);
  const cancel = () => release(false);
  root.addEventListener("pointerdown", down, true);
  root.addEventListener("pointermove", move, true);
  root.addEventListener("pointerup", up, true);
  root.addEventListener("pointercancel", cancel, true);
  return () => {
    release(false);
    root.removeEventListener("pointerdown", down, true);
    root.removeEventListener("pointermove", move, true);
    root.removeEventListener("pointerup", up, true);
    root.removeEventListener("pointercancel", cancel, true);
  };
}

/** A sheet is let go when it is pulled this share of its height, or flicked at this speed (px/ms). */
const SHEET_DISMISS_SHARE = 0.32;
const SHEET_DISMISS_SPEED = 0.55;
const SHEET_DRAG_START_PX = 6;
/** Upward the sheet has nowhere to go: it gives a little and pulls back. */
const SHEET_RESIST_PX = 28;

/**
 * Drag a sheet down to close it: by its head at any time, by its body once it is scrolled to the top. The sheet follows
 * the finger and the scrim thins with it; on release it either falls from where it is or springs back.
 */
export function useSheetDrag(panel: RefObject<HTMLElement | null>, host: RefObject<HTMLElement | null>, enabled: boolean, close: () => void): void {
  useEffect(() => {
    const sheet = panel.current;
    const layer = host.current;
    if (!enabled || !sheet || !layer) return;
    type Gesture = { id: number; x: number; y: number; head: boolean; dragging: boolean; height: number; offset: number; lastY: number; lastAt: number; speed: number };
    let gesture: Gesture | null = null;
    const touchOf = (event: TouchEvent, id: number) => Array.from(event.changedTouches).find((touch) => touch.identifier === id);
    const clear = () => {
      layer.removeAttribute("data-drag");
      layer.style.removeProperty("--m-sheet-pull");
      sheet.style.removeProperty("transform");
    };
    const start = (event: TouchEvent) => {
      if (gesture || event.touches.length > 1) return;
      const target = event.target as Element;
      if (target.closest("input, textarea, select")) return;
      const touch = event.changedTouches[0];
      gesture = { id: touch.identifier, x: touch.clientX, y: touch.clientY, head: Boolean(target.closest(".m-sheet-head")), dragging: false, height: 0, offset: 0, lastY: touch.clientY, lastAt: event.timeStamp, speed: 0 };
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      const touch = touchOf(event, gesture.id);
      if (!touch) return;
      const dy = touch.clientY - gesture.y;
      if (!gesture.dragging) {
        const dx = touch.clientX - gesture.x;
        if (Math.abs(dy) < SHEET_DRAG_START_PX && Math.abs(dx) < SHEET_DRAG_START_PX) return;
        const pullsDown = dy > 0 && Math.abs(dy) > Math.abs(dx);
        if (!pullsDown || (!gesture.head && sheet.scrollTop > 0)) { gesture = null; return; }
        gesture.dragging = true;
        gesture.height = sheet.offsetHeight;
        layer.setAttribute("data-drag", "");
      }
      if (event.cancelable) event.preventDefault();
      const elapsed = event.timeStamp - gesture.lastAt;
      if (elapsed > 0) gesture.speed = (touch.clientY - gesture.lastY) / elapsed;
      gesture.lastY = touch.clientY;
      gesture.lastAt = event.timeStamp;
      gesture.offset = dy >= 0 ? dy : -SHEET_RESIST_PX * (1 - Math.exp(dy / SHEET_RESIST_PX));
      sheet.style.transform = `translateY(${gesture.offset.toFixed(1)}px)`;
      layer.style.setProperty("--m-sheet-pull", String(Math.max(0, Math.min(1, gesture.offset / gesture.height))));
    };
    const end = (event: TouchEvent) => {
      if (!gesture || !touchOf(event, gesture.id)) return;
      const finished = gesture;
      gesture = null;
      if (!finished.dragging) return;
      // A finger that stopped before lifting has no speed, whatever its last sample said.
      const speed = event.timeStamp - finished.lastAt > 80 ? 0 : finished.speed;
      const dismiss = event.type === "touchend" && finished.offset > 8 && (finished.offset > finished.height * SHEET_DISMISS_SHARE || speed > SHEET_DISMISS_SPEED);
      clear();
      if (dismiss) close();
    };
    sheet.addEventListener("touchstart", start, { passive: true });
    sheet.addEventListener("touchmove", move, { passive: false });
    sheet.addEventListener("touchend", end, { passive: true });
    sheet.addEventListener("touchcancel", end, { passive: true });
    return () => {
      sheet.removeEventListener("touchstart", start);
      sheet.removeEventListener("touchmove", move);
      sheet.removeEventListener("touchend", end);
      sheet.removeEventListener("touchcancel", end);
      clear();
    };
  }, [panel, host, enabled, close]);
}

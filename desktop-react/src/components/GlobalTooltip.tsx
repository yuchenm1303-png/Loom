import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";
import "./global-tooltip.css";

/** A control that is a glyph alone names itself sooner than one whose words are already on screen. */
const DELAY_GLYPH = 420;
const DELAY_WORDS = 800;
/** Moving on from one named control to the next, the next one's name comes at once. */
const DELAY_WARM = 40;
const WARM_FOR = 600;
/** Leaving a control, its name waits this long for the pointer to reach the next one, so it can glide there. */
const GRACE = 90;
/** The system's own tooltip waits about half a second; the title is held well before, so it never gets to show. */
const HOLD_AFTER = 120;
const GAP = 6;
const EDGE = 8;
/** The quick-tool dock names its tools itself; anything marked data-tooltip="none" keeps the browser's own. */
const SKIP = '.loom-tool-rail, [data-tooltip="none"]';
/** "New conversation · Ctrl N": the shortcut after the name is set in the quieter ink of a key. */
const SHORTCUT = /^(.+?) · ((?:Ctrl|Alt|Shift|Cmd|Win|⌘|⌥|⇧|⌃)\b.*|F\d{1,2})$/;

/**
 * The app's tooltips. Every `title` in the app is shown here, in the app's own material, instead of by the
 * operating system (which took about a second, in a white box of its own): one tooltip element for the whole window,
 * placed under the control it names (above it when there is no room), on the scale of every other popover (readable
 * on its first frame, gone on fast-out). A moment after the pointer arrives on a control its `title` is held in
 * `data-loom-title`, so the system's tooltip has nothing to show, and it is put back when the pointer leaves; keyboard
 * focus shows the same tooltip and leaves the attribute alone (the system never shows one for focus).
 */
export function GlobalTooltip() {
  const tipRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const tip = tipRef.current;
    if (!tip) return;
    let target: HTMLElement | null = null;
    let fromPointer = false;
    let shown = false;
    let timer = 0;
    let watchTimer = 0;
    let leaveTimer = 0;
    let holdTimer = 0;
    let warmUntil = 0;
    let pressed: HTMLElement | null = null;
    const write = (text: string) => {
      const match = SHORTCUT.exec(text);
      if (!match) {
        tip.textContent = text;
        return;
      }
      const keys = document.createElement("span");
      keys.className = "loom-tooltip-keys";
      keys.textContent = match[2];
      tip.replaceChildren(document.createTextNode(match[1]), keys);
    };
    const watch = new MutationObserver(() => {
      // React set a new title while ours was held (a "Copied" after a copy): show it, and hold it again.
      if (!target || !fromPointer || !target.hasAttribute("title")) return;
      const text = target.getAttribute("title") ?? "";
      hold(target);
      if (shown) {
        write(text);
        place(target);
      }
    });

    const textOf = (element: HTMLElement) => (element.getAttribute("title") ?? element.dataset.loomTitle ?? "").trim();
    const hold = (element: HTMLElement) => {
      const title = element.getAttribute("title");
      if (title === null) return;
      element.dataset.loomTitle = title;
      element.removeAttribute("title");
    };
    const restore = (element: HTMLElement) => {
      const title = element.dataset.loomTitle;
      if (title === undefined) return;
      delete element.dataset.loomTitle;
      if (!element.hasAttribute("title")) element.setAttribute("title", title);
    };
    const named = (node: EventTarget | null): HTMLElement | null => {
      if (!(node instanceof Element)) return null;
      const element = node.closest<HTMLElement>("[title], [data-loom-title]");
      if (!element || element.closest(SKIP) || !textOf(element)) return null;
      return element;
    };
    const zoom = () => Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1;

    const place = (element: HTMLElement) => {
      const scale = zoom();
      const box = element.getBoundingClientRect();
      const width = tip.offsetWidth * scale;
      const height = tip.offsetHeight * scale;
      const x = Math.max(EDGE, Math.min(innerWidth - EDGE - width, box.left + box.width / 2 - width / 2));
      let y = box.bottom + GAP;
      const above = y + height > innerHeight - EDGE;
      if (above) y = box.top - GAP - height;
      tip.dataset.side = above ? "above" : "below";
      tip.style.translate = `${Math.round(x / scale)}px ${Math.round(Math.max(EDGE, y) / scale)}px`;
    };

    // Let go of the control (its title back, the timers off); the tooltip itself fades unless the next one is about
    // to take its place.
    const release = (keepTip: boolean) => {
      window.clearTimeout(timer);
      window.clearTimeout(leaveTimer);
      window.clearTimeout(holdTimer);
      window.clearInterval(watchTimer);
      watch.disconnect();
      if (target && fromPointer) restore(target);
      if (shown) warmUntil = performance.now() + WARM_FOR;
      shown = false;
      target = null;
      if (!keepTip) tip.dataset.on = "false";
    };
    const hide = () => release(false);

    const show = () => {
      if (!target || !target.isConnected) return hide();
      const text = textOf(target);
      if (!text) return hide();
      if (fromPointer) hold(target);
      const moving = tip.dataset.on === "true";
      write(text);
      if (!moving && !prefersReducedMotionNow()) {
        // Readable on its first frame, then settled, like every popover.
        tip.style.transition = "none";
        tip.style.opacity = ".72";
        void tip.offsetWidth;
        tip.style.transition = "";
        tip.style.opacity = "";
      }
      tip.dataset.glide = moving ? "true" : "false";
      place(target);
      tip.dataset.on = "true";
      shown = true;
      if (fromPointer) watch.observe(target, { attributes: true, attributeFilter: ["title"] });
      // A control that goes away under its tooltip (a row closed, a panel swapped) takes it along.
      watchTimer = window.setInterval(() => { if (!target?.isConnected) hide(); }, 250);
    };

    const schedule = (element: HTMLElement, pointer: boolean) => {
      if (element === target) return;
      const wasShown = shown || tip.dataset.on === "true";
      release(wasShown);
      target = element;
      fromPointer = pointer;
      const glyph = !(element.textContent ?? "").trim();
      const warm = wasShown || performance.now() < warmUntil;
      timer = window.setTimeout(show, warm ? DELAY_WARM : glyph ? DELAY_GLYPH : DELAY_WORDS);
      if (pointer) holdTimer = window.setTimeout(() => { if (target === element) hold(element); }, HOLD_AFTER);
    };

    const onOver = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      const element = named(event.target);
      if (!element || element === pressed) return;
      schedule(element, true);
    };
    const onOut = (event: PointerEvent) => {
      if (pressed && !(event.relatedTarget instanceof Node && pressed.contains(event.relatedTarget))) pressed = null;
      if (!target || !fromPointer) return;
      if (event.relatedTarget instanceof Node && target.contains(event.relatedTarget)) return;
      if (!shown) return hide();
      // Shown: the control gets its title back now, and its name waits a moment for the pointer to reach the next
      // named control, so the tooltip can glide there instead of going and coming back.
      release(true);
      leaveTimer = window.setTimeout(() => { tip.dataset.on = "false"; }, GRACE);
    };
    const onDown = () => {
      if (target && fromPointer) pressed = target;
      hide();
      warmUntil = 0;
    };
    const onFocus = (event: FocusEvent) => {
      const element = named(event.target);
      if (!element || !(event.target as Element).matches(":focus-visible")) return;
      schedule(element, false);
    };
    const onBlur = () => { if (target && !fromPointer) hide(); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" || (target && fromPointer)) hide(); };
    const onAway = () => hide();

    document.addEventListener("pointerover", onOver, true);
    document.addEventListener("pointerout", onOut, true);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("focusin", onFocus, true);
    document.addEventListener("focusout", onBlur, true);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("scroll", onAway, { capture: true, passive: true });
    document.addEventListener("wheel", onAway, { capture: true, passive: true });
    window.addEventListener("blur", onAway);
    document.addEventListener("visibilitychange", onAway);
    return () => {
      hide();
      document.removeEventListener("pointerover", onOver, true);
      document.removeEventListener("pointerout", onOut, true);
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("focusin", onFocus, true);
      document.removeEventListener("focusout", onBlur, true);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("scroll", onAway, { capture: true });
      document.removeEventListener("wheel", onAway, { capture: true });
      window.removeEventListener("blur", onAway);
      document.removeEventListener("visibilitychange", onAway);
    };
  }, []);
  return createPortal(<div ref={tipRef} className="loom-tooltip" role="tooltip" aria-hidden="true" data-on="false" />, document.body);
}

/* The 对话 / 建造 switch as one object moving: the chat composer is the build hub, carried between two spaces.
 *
 * To build: the composer's contents and the transcript fade where they are (lift), an empty shell of the
 * composer's material takes its box and sets off at once (rise) for where the hub was last seen. Nothing is
 * opened while it travels: the shell's place and size are animated on the main thread, and building a view
 * under it would stop it in the air. Parked, it waits for the conversation to open and the hub to be drawn,
 * settles on the hub's real box if that differs, and is the hub: its contents fade in, the cables grow out of
 * it and the cards appear at their ends (landed).
 * Back to chat: the cables retract and the cards and the hub's contents fade (unplug), the shell takes the
 * hub's box and carries it down (sink), the composer's place is filled the same way, and the composer's
 * contents and the transcript fade in (arrive).
 *
 * The two views are never mounted together: they show different conversations, so the old one stays until
 * the shell is parked and they swap, unseen, around it. Text only fades; only the empty shell travels.
 * Styles: project-mode-toggle.css (stage, shell, composer) and project-flow.css (the hub's cards and cables).
 * Reduced motion: an instant swap. */
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { DUR, DUR_OUT, SPRING } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

export type Mode = "chat" | "project";
export type MorphPhase = "lift" | "rise" | "landed" | "unplug" | "sink" | "arrive";
type Look = ReturnType<typeof look>;

const CHAT = ":scope > .composer-stage .composer";
const HUB = ":scope > .project-mode-stage .pf-composer";
/** The main pane (the one whose header holds the switch); a split pane beside it is left alone. */
const pane = () => document.querySelector(".pf-toggle")?.closest<HTMLElement>(".workspace") ?? null;
const CLEAR = "rgba(0, 0, 0, 0) 0px 0px 0px 0px";
/** How long the parked shell waits for the other view to be drawn once its conversation is open (a hub's overview may still be loading). */
const WAIT_MS = 1500;
/** Where each end's box was last seen, in this pane's geometry: for the conversation it belongs to, and for its mode. */
const seen = new Map<string, Look>();

function look(node: HTMLElement) {
  const box = node.getBoundingClientRect(), style = getComputedStyle(node);
  return {
    box: { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px`,
           borderRadius: style.borderTopLeftRadius, backgroundColor: style.backgroundColor,
           borderColor: parseFloat(style.borderTopWidth) ? style.borderTopColor : "rgba(0, 0, 0, 0)" },
    center: { x: box.left + box.width / 2, y: box.top + box.height / 2 },
    shadow: style.boxShadow === "none" ? [] : style.boxShadow.split(/,(?![^(]*\))/).map((item) => item.trim()),
  };
}

const seenKey = (mode: Mode, place = "") => {
  const box = pane()?.getBoundingClientRect();
  return `${Math.round(box?.left ?? 0)},${Math.round(box?.width ?? 0)},${Math.round(box?.height ?? 0)}:${mode}:${place}`;
};
function remember(mode: Mode, place: string | undefined, seenLook: Look) {
  seen.set(seenKey(mode), seenLook);
  if (place) seen.set(seenKey(mode, place), seenLook);
}

/** Where the other end will be. Never seen in this pane's size: laid out as the hub and the composer lay themselves out;
 *  the shell settles on the real box when it is drawn. */
function expected(mode: Mode, place: string | undefined, from: Look): Look {
  const known = (place && seen.get(seenKey(mode, place))) || seen.get(seenKey(mode));
  if (known) return known;
  const area = pane()?.getBoundingClientRect();
  if (!area) return from;
  const width = mode === "project" ? Math.max(340, Math.min(460, area.width * .4)) : Math.min(860, area.width - 64);
  const height = mode === "project" ? 171 : 107;
  const top = mode === "project" ? area.top + area.height / 2 - 10 : area.bottom - 38 - height;
  const left = area.left + (area.width - width) / 2;
  return { ...from, box: { ...from.box, left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` },
           center: { x: left + width / 2, y: top + height / 2 } };
}

const sameLook = (a: Look, b: Look) => (Object.keys(a.box) as (keyof Look["box"])[]).every((key) =>
  key.endsWith("Color") || key === "borderRadius" ? a.box[key] === b.box[key] : Math.abs(parseFloat(a.box[key]) - parseFloat(b.box[key])) < 1);

/** An empty shell of `from`'s material, laid exactly over it, that can travel to another look. */
function shellOver(from: HTMLElement) {
  let current = look(from);
  const shell = document.createElement("div");
  shell.className = "mode-shell";
  shell.setAttribute("aria-hidden", "true");
  Object.assign(shell.style, current.box, { boxShadow: current.shadow.join(", ") || "none" });
  document.body.append(shell);
  return {
    look: current,
    /** There at `end`, from wherever it is now. On the hub's own curve: `EASE.move` was nine tenths there in a third of
     *  the time, and the crawl after it read as the shell getting stuck on its way. */
    fly(end: Look, duration: number = DUR.flow) {
      // Shadow lists of different lengths do not interpolate: pad the shorter with clear shadows.
      const count = Math.max(current.shadow.length, end.shadow.length);
      const shadow = (list: string[]) => [...list, ...Array(count - list.length).fill(CLEAR)].join(", ") || "none";
      const flight = shell.animate([{ ...current.box, boxShadow: shadow(current.shadow) }, { ...end.box, boxShadow: shadow(end.shadow) }],
                                   { duration, easing: SPRING.settle, fill: "forwards" });
      current = end;
      return flight.finished.then(() => undefined, () => undefined);
    },
    /** Where it was last sent. */
    at: () => current,
    remove() { shell.remove(); },
  };
}

/** The element `selector` names, once it is drawn: polled each frame, up to `WAIT_MS`. */
function drawn(selector: string, alive: () => boolean) {
  return new Promise<HTMLElement | null>((resolve) => {
    const until = performance.now() + WAIT_MS;
    const check = () => {
      const node = pane()?.querySelector<HTMLElement>(selector);
      if (!alive()) resolve(null);
      else if (node && node.getBoundingClientRect().width) resolve(node);
      else if (performance.now() > until) resolve(null);
      else requestAnimationFrame(check);
    };
    check();
  });
}

/** Writes the phase on the views it concerns. Phases are attributes, not React state: a phase
 *  change re-rendering the whole app cost the landing frame ~50 ms. */
function mark(phase: MorphPhase | null) {
  for (const [selector, name] of [[".project-mode-stage", "morph"], [".conversation-stage", "modeMorph"], [".composer-stage", "modeMorph"]] as const) {
    for (const node of pane()?.querySelectorAll<HTMLElement>(`:scope > ${selector}`) ?? []) {
      if (phase) node.dataset[name] = phase; else delete node.dataset[name];
    }
  }
}

/** The header's switch: `carry(mode, open, place)` moves to the other space, `open` being what opens its conversation
 *  and `place` that conversation's id, if there is one yet. Rejects if `open` does. Opening a conversation any other
 *  way ends a carry still in the air only by landing it. */
export function useModeMorph() {
  const count = useRef(0);
  const timers = useRef<number[]>([]);
  const shell = useRef<{ remove(): void } | null>(null);
  const phase = useRef<MorphPhase | null>(null);
  const stop = useCallback(() => {
    count.current += 1;  // whatever is still in flight belongs to a carry that is over
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    shell.current?.remove();
    shell.current = null;
    phase.current = null;
    mark(null);
  }, []);
  useEffect(() => stop, [stop]);
  // A render may have read a phase that has moved on since.
  useLayoutEffect(() => { if (phase.current) mark(phase.current); });
  /** The phase for a view being rendered. It has to be on the element from its birth: the views measure themselves as they
   *  mount (the transcript goes to its end), so a mark written after them would make the hidden state a fade from visible. */
  const renderedPhase = () => phase.current ?? undefined;

  const carry = useCallback(async (mode: Mode, open: () => Promise<unknown>, place?: string) => {
    stop();
    const toBuild = mode === "project";
    if (prefersReducedMotionNow() || document.hidden || !pane()?.querySelector(toBuild ? CHAT : HUB)) { await open(); return; }
    const id = count.current;
    const current = () => count.current === id;
    const after = (ms: number) => new Promise<void>((resolve) => { timers.current.push(window.setTimeout(resolve, ms)); });
    const enter = (next: MorphPhase) => { phase.current = next; mark(next); };
    try {
      enter(toBuild ? "lift" : "unplug");
      await after(DUR_OUT.fast);
      // Another carry or a navigation took over during the exit: the conversation still has to open.
      const from = current() && pane()?.querySelector<HTMLElement>(toBuild ? CHAT : HUB);
      if (!from) { await open(); stop(); return; }
      // The shell takes the box on the frame its owner hides it, and leaves at once.
      const carried = shellOver(from);
      shell.current = carried;
      remember(toBuild ? "chat" : "project", undefined, carried.look);
      enter(toBuild ? "rise" : "sink");
      await carried.fly(expected(mode, place, carried.look));
      if (!current()) { await open(); return; }
      // Parked: nothing moves now, so this is when the other view can take its frames.
      await open();
      if (!current()) return;
      const target = await drawn(toBuild ? HUB : CHAT, current);
      if (!current()) return;
      if (!target) { stop(); return; }
      const end = look(target);
      remember(mode, place, end);
      if (!sameLook(carried.at(), end)) {
        await carried.fly(end, DUR.fast);
        if (!current()) return;
      }
      // The target shows on the same frame the shell goes, so the hand-over has no gap.
      enter(toBuild ? "landed" : "arrive");
      carried.remove();
      shell.current = null;
      // To build, the cables grow for a flow step and the cards follow a press later; to chat, the contents fade in.
      await after(toBuild ? DUR.flow + DUR.press : DUR.base);
      if (current()) stop();
    } catch (cause) {
      stop();
      throw cause;
    }
  }, [stop]);

  return { carry, renderedPhase };
}

/* The 对话 / 建造 switch as one object moving: the chat composer is the build hub.
 *
 * To build: the composer's contents fade where they are (lift), then an empty shell of the
 * composer's material carries its box up into the hub's place and takes the hub's look (rise),
 * while the build canvas covers the conversation. Landed, the shell is the hub: its contents fade
 * in, the cables grow out of it and the cards appear at their ends (landed).
 * Back to chat: the cables retract into the hub and the cards and the hub's contents fade
 * (unplug), the shell carries the hub down into the composer's place while the canvas lifts off
 * the conversation (sink), and the composer's contents fade in (arrive).
 *
 * Text only fades; only the empty shell travels. Styles: project-mode-toggle.css (stage, shell,
 * composer) and project-flow.css (the hub's cards and cables). Reduced motion: an instant swap. */
import { startTransition, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { DUR, DUR_OUT, EASE, travelMs } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

export type Mode = "chat" | "project";
export type MorphPhase = "lift" | "rise" | "landed" | "unplug" | "sink" | "arrive";
interface Morph { id: number; to: Mode; /** When the outgoing contents have left: the box sets off then. */ exitEnd: number }

const CHAT = ":scope > .composer-stage .composer";
const HUB = ":scope > .project-mode-stage .pf-composer";
/** The main pane (the one whose header holds the switch); a split pane beside it is left alone. */
const pane = () => document.querySelector(".pf-toggle")?.closest<HTMLElement>(".workspace") ?? null;
const CLEAR = "rgba(0, 0, 0, 0) 0px 0px 0px 0px";
/** How long the shell waits at the composer for the hub to be drawn (its overview is still loading). */
const WAIT_MS = 1500;

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

/** An empty shell of `from`'s material, laid exactly over it, that can travel to another element and take its look. */
function shellOver(from: HTMLElement) {
  const start = look(from);
  const shell = document.createElement("div");
  shell.className = "mode-shell";
  shell.setAttribute("aria-hidden", "true");
  Object.assign(shell.style, start.box, { boxShadow: start.shadow.join(", ") || "none" });
  document.body.append(shell);
  return {
    fly(to: HTMLElement) {
      const end = look(to);
      // Shadow lists of different lengths do not interpolate: pad the shorter with clear shadows.
      const count = Math.max(start.shadow.length, end.shadow.length);
      const shadow = (list: string[]) => [...list, ...Array(count - list.length).fill(CLEAR)].join(", ") || "none";
      const duration = travelMs(Math.hypot(end.center.x - start.center.x, end.center.y - start.center.y));
      const flight = shell.animate([{ ...start.box, boxShadow: shadow(start.shadow) }, { ...end.box, boxShadow: shadow(end.shadow) }],
                                   { duration, easing: EASE.move, fill: "forwards" });
      return flight.finished.then(() => undefined, () => undefined);
    },
    remove() { shell.remove(); },
  };
}

/** The element `selector` names, once it is drawn: polled each frame, up to `WAIT_MS`. */
function drawn(selector: string, alive: () => boolean) {
  return new Promise<HTMLElement | null>((resolve) => {
    const until = performance.now() + WAIT_MS;
    const look = () => {
      const node = pane()?.querySelector<HTMLElement>(selector);
      if (!alive()) resolve(null);
      else if (node && node.getBoundingClientRect().width) resolve(node);
      else if (performance.now() > until) resolve(null);
      else requestAnimationFrame(look);
    };
    look();
  });
}

/** Writes the phase on the views it concerns. Phases are attributes, not React state: a phase
 *  change re-rendering the whole app cost the landing frame ~50 ms. React renders only twice per
 *  morph, when it starts (both views mount) and when it is over (one unmounts). */
function mark(phase: MorphPhase | null) {
  for (const [selector, name] of [[".project-mode-stage", "morph"], [".conversation-stage", "modeMorph"], [".composer-stage", "modeMorph"]] as const) {
    for (const node of pane()?.querySelectorAll<HTMLElement>(`:scope > ${selector}`) ?? []) {
      if (phase) node.dataset[name] = phase; else delete node.dataset[name];
    }
  }
}

/** The mode switch with its morph. `change` is what the header's switch calls; a different conversation never morphs.
 *  `morph` (which way, while it runs) is what keeps both views mounted. */
export function useModeMorph(threadId: string | undefined, setMode: (mode: Mode) => void) {
  const [morph, setMorph] = useState<Morph | null>(null);
  const count = useRef(0);
  const timers = useRef<number[]>([]);
  const shell = useRef<{ remove(): void } | null>(null);
  const stop = useCallback(() => {
    count.current += 1;  // whatever is still in flight belongs to a morph that is over
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    shell.current?.remove();
    shell.current = null;
    mark(null);
    setMorph(null);
  }, []);
  useEffect(() => stop, [threadId, stop]);

  const change = useCallback((mode: Mode) => {
    stop();
    if (prefersReducedMotionNow() || document.hidden || !pane()?.querySelector(mode === "project" ? CHAT : HUB)) { setMode(mode); return; }
    // The outgoing contents start leaving on this very frame, written on the DOM; mounting the other
    // view (the transcript is ~100 ms of work) renders in the background meanwhile, in slices.
    mark(mode === "project" ? "lift" : "unplug");
    const morph = { id: count.current, to: mode, exitEnd: performance.now() + DUR_OUT.fast };
    startTransition(() => { setMode(mode); setMorph(morph); });
  }, [setMode, stop]);

  useLayoutEffect(() => {
    if (!morph) return;
    const { id, to, exitEnd } = morph;
    const current = () => count.current === id;
    const after = (ms: number) => new Promise<void>((resolve) => { timers.current.push(window.setTimeout(resolve, ms)); });
    // The view that just mounted starts in the same phase, before its first paint.
    mark(to === "project" ? "lift" : "unplug");
    void (async () => {
      await after(Math.max(0, exitEnd - performance.now()));
      const from = current() && pane()?.querySelector<HTMLElement>(to === "project" ? CHAT : HUB);
      if (!from) return;
      // The shell takes the box on the frame its owner hides it.
      const carried = shellOver(from);
      shell.current = carried;
      mark(to === "project" ? "rise" : "sink");
      const target = await drawn(to === "project" ? HUB : CHAT, current);
      if (!current()) return;
      if (!target) { stop(); return; }
      await carried.fly(target);
      if (!current()) return;
      // The target shows on the same frame the shell goes, so the hand-over has no gap.
      mark(to === "project" ? "landed" : "arrive");
      carried.remove();
      shell.current = null;
      // To build, the cables grow for a flow step and the cards follow a press later; to chat, the contents fade in.
      await after(to === "project" ? DUR.flow + DUR.press : DUR.base);
      // The view that leaves unmounts before its marks go, or it would paint one frame as it is when unmarked.
      if (current()) { flushSync(() => setMorph(null)); mark(null); }
    })();
  }, [morph]);  // eslint-disable-line react-hooks/exhaustive-deps

  return { morph, change };
}

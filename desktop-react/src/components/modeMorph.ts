/* The 对话 / 建造 switch as one object moving: the chat composer is the build hub, carried between two spaces.
 *
 * To build: the composer's contents fade where they are (lift), an empty shell of the composer's
 * material takes its box (rise) and waits there while the other space's conversation opens. Once the
 * hub is drawn the shell carries the box up into its place and takes its look. Landed, the shell is
 * the hub: its contents fade in, the cables grow out of it and the cards appear at their ends (landed).
 * Back to chat: the cables retract and the cards and the hub's contents fade (unplug), the shell takes
 * the hub's box and waits; then it carries the box down into the composer's place (sink), and the
 * composer's contents fade in (arrive).
 *
 * The two views are never mounted together: they show different conversations, so the old one stays
 * until the new one is ready and they swap under the shell. Text only fades; only the empty shell
 * travels. Styles: project-mode-toggle.css (stage, shell, composer) and project-flow.css (the hub's
 * cards and cables). Reduced motion: an instant swap. */
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { DUR, DUR_OUT, EASE, travelMs } from "../motion/tokens";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";

export type Mode = "chat" | "project";
export type MorphPhase = "lift" | "rise" | "landed" | "unplug" | "sink" | "arrive";

const CHAT = ":scope > .composer-stage .composer";
const HUB = ":scope > .project-mode-stage .pf-composer";
/** The main pane (the one whose header holds the switch); a split pane beside it is left alone. */
const pane = () => document.querySelector(".pf-toggle")?.closest<HTMLElement>(".workspace") ?? null;
const CLEAR = "rgba(0, 0, 0, 0) 0px 0px 0px 0px";
/** How long the shell waits for the other view to be drawn once its conversation is open (a hub's overview is still loading). */
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
 *  change re-rendering the whole app cost the landing frame ~50 ms. */
function mark(phase: MorphPhase | null) {
  for (const [selector, name] of [[".project-mode-stage", "morph"], [".conversation-stage", "modeMorph"], [".composer-stage", "modeMorph"]] as const) {
    for (const node of pane()?.querySelectorAll<HTMLElement>(`:scope > ${selector}`) ?? []) {
      if (phase) node.dataset[name] = phase; else delete node.dataset[name];
    }
  }
}

/** The header's switch: `carry(mode, open)` moves to the other space, `open` being what opens its conversation.
 *  Rejects if `open` does. Opening a conversation any other way ends a carry still in the air only by landing it. */
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
  // The view that mounts on the swap starts in the current phase, before its first paint.
  useLayoutEffect(() => { if (phase.current) mark(phase.current); });

  const carry = useCallback(async (mode: Mode, open: () => Promise<unknown>) => {
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
      // The shell takes the box on the frame its owner hides it.
      const carried = shellOver(from);
      shell.current = carried;
      if (toBuild) enter("rise");
      // Only now: a conversation that opens at once would otherwise swap the views before the box was taken.
      await open();
      if (!current()) return;
      if (!toBuild) enter("sink");
      const target = await drawn(toBuild ? HUB : CHAT, current);
      if (!current()) return;
      if (!target) { stop(); return; }
      await carried.fly(target);
      if (!current()) return;
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

  return { carry };
}

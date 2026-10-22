/**
 * The motion scale, for the code that has to know how long something takes.
 *
 * motion-tokens.css is the source; this file repeats its numbers because a timer, a Web Animations call or a
 * `useMotionPresence` retention cannot read a stylesheet cheaply. tests/test_motion_scale_contract.py keeps the two
 * equal, so a number is never changed in one place only. Write a duration as one of these, not as a literal.
 */

/** What arrives, in milliseconds. */
export const DUR = {
  /** The answer of a press. */
  press: 90,
  /** Whatever answers a hand: hover, focus, tooltips, popovers, menus, toasts (and every dock leaves on fast-out). */
  fast: 150,
  /** What appears in place: dialogs, pages, scrims, a block of content, a short fold. */
  base: 240,
  /** What changes size or place: a long fold, a switch, the travel of a mark. */
  flow: 320,
  /** The docks' arrival and the layout move that goes with them (the a61 clock). */
  panel: 390,
  /** What is drawn rather than shown: a bar filling, a spring settling, a glow. */
  slow: 520,
} as const;

/** What leaves. Never the slow half of an interaction. */
export const DUR_OUT = {
  fast: 120,
  base: 200,
  flow: 260,
} as const;

/** The gap between neighbours that arrive one after the other. */
export const STAGGER_MS = 40;

/** A surface that leaves stays mounted this much longer than its exit lasts, so the exit is never cut. */
export const RETAIN_MARGIN_MS = 35;

/**
 * How long `useMotionPresence` keeps a surface mounted after it is closed. One name per kind of surface: the exit
 * of what it is (plus the margin; a dock leaves on fast-out, so it is `fast`), the length of a fold (it takes the
 * same step both ways), and `panel`, the clock of the layout move that goes with a dock.
 */
export const RETAIN = {
  fast: DUR_OUT.fast + RETAIN_MARGIN_MS,
  base: DUR_OUT.base + RETAIN_MARGIN_MS,
  flow: DUR_OUT.flow + RETAIN_MARGIN_MS,
  /** A short fold: it takes `base` both ways. */
  foldBase: DUR.base + RETAIN_MARGIN_MS,
  /** A fold of a block or a list: it takes `flow` both ways. */
  fold: DUR.flow + RETAIN_MARGIN_MS,
  panel: DUR.panel + 30,
} as const;

/** The curves, as Web Animations wants them. */
export const EASE = {
  out: "cubic-bezier(.16, .78, .18, 1)",
  move: "cubic-bezier(.22, .7, .18, 1)",
  exit: "cubic-bezier(.3, .3, .55, 1)",
  inout: "cubic-bezier(.4, 0, .2, 1)",
  glide: "cubic-bezier(.2, 0, 0, 1)",
} as const;

/** The springs, for a Web Animations call that needs one (a text-free mark landing). */
export const SPRING = {
  settle: "linear(0, 0.028 3.8%, 0.097 7.7%, 0.189 11.5%, 0.291 15.4%, 0.393 19.2%, 0.491 23.1%, 0.581 26.9%, 0.661 30.8%, 0.73 34.6%, 0.789 38.5%, 0.838 42.3%, 0.878 46.2%, 0.91 50%, 0.936 53.8%, 0.956 57.7%, 0.971 61.5%, 0.982 65.4%, 0.99 69.2%, 0.996 73.1%, 1 76.9%, 1.002 80.8%, 1.004 84.6%, 1.005 88.5%, 1.005 92.3%, 1.005 96.2%, 1)",
  soft: "linear(0, .038 3.8%, .13 7.7%, .251 11.5%, .382 15.4%, .51 19.2%, .627 23.1%, .729 26.9%, .814 30.8%, .882 34.6%, .934 38.5%, .972 42.3%, .999 46.2%, 1.016 50%, 1.026 53.8%, 1.031 57.7%, 1.031 61.5%, 1.03 65.4%, 1.027 69.2%, 1.023 73.1%, 1.018 76.9%, 1.015 80.8%, 1.011 84.6%, 1.008 88.5%, 1.005 92.3%, 1.003 96.2%, 1)",
  lively: "linear(0, .068 3.8%, .231 7.7%, .433 11.5%, .636 15.4%, .813 19.2%, .951 23.1%, 1.046 26.9%, 1.101 30.8%, 1.124 34.6%, 1.123 38.5%, 1.107 42.3%, 1.083 46.2%, 1.057 50%, 1.033 53.8%, 1.013 57.7%, .999 61.5%, .99 65.4%, .985 69.2%, .984 73.1%, .985 76.9%, .988 80.8%, .991 84.6%, .995 88.5%, .997 92.3%, .999 96.2%, 1)",
  pop: "linear(0, 0.035, 0.125, 0.248, 0.387, 0.528, 0.662, 0.781, 0.881, 0.962, 1.023, 1.065, 1.091, 1.104, 1.107, 1.101, 1.091, 1.077, 1.062, 1.047, 1.033, 1.021, 1.01, 1.002, 0.996, 0.992, 0.99, 0.989, 0.989, 0.989, 0.991, 0.992, 0.994, 0.995, 0.997, 0.998, 0.999, 1, 1.001, 1.001, 1)",
} as const;

/**
 * How long a text-free mark (a selection pill, an indicator) takes to travel `distance` px: the `flow` step for a hop,
 * a little longer for a long way, never longer than the `panel` step.
 */
export function travelMs(distance: number): number {
  return Math.round(Math.min(DUR.panel, DUR.flow + Math.abs(distance) * 0.25));
}

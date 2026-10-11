/**
 * The UI face is a variable font, and every weight the stylesheets ask for is an instance of it that is made the first
 * time text is laid out in that weight: 7-10ms each. They used to be paid in the first frame of whichever surface met
 * a weight first (the settings page asks for six it had not seen: about 55ms of its first open). Laying out two letters
 * in each of them while the app is idle moves that to a time nobody is waiting on. Sizes are cheap (the instance is
 * shared), so one size is enough.
 *
 * The list is every numeric `font-weight` the source uses; scripts/tests/font-warmup.test.mjs fails when a
 * stylesheet asks for one that is not here.
 */
export const UI_FONT_WEIGHTS: readonly number[] = [
  400, 450, 500, 520, 530, 540, 545, 550, 560, 570, 580, 590, 600, 610, 620, 630,
  640, 650, 660, 670, 680, 690, 700, 710, 720, 730, 740, 750, 760, 780, 790, 800,
];

const IDLE_TIMEOUT_MS = 2500;

type Idle = (callback: () => void, options: { timeout: number }) => number;
type CancelIdle = (handle: number) => void;

/** One weight per idle slice, starting from the weights used most. Returns what stops it. */
export function warmFontWeights(): () => void {
  if (typeof document === "undefined" || !document.body) return () => {};
  const idle: Idle = typeof window.requestIdleCallback === "function"
    ? (callback, options) => window.requestIdleCallback(callback, options)
    : (callback) => window.setTimeout(callback, 120);
  const cancelIdle: CancelIdle = typeof window.cancelIdleCallback === "function"
    ? (handle) => window.cancelIdleCallback(handle)
    : (handle) => window.clearTimeout(handle);
  const probe = document.createElement("span");
  probe.setAttribute("aria-hidden", "true");
  probe.textContent = "Aa";
  probe.style.cssText = "position:fixed;left:-9999px;top:0;visibility:hidden;pointer-events:none;white-space:pre;contain:layout style";
  document.body.appendChild(probe);
  const queue = [...UI_FONT_WEIGHTS];
  let handle = 0;
  let stopped = false;
  const step = () => {
    handle = 0;
    if (stopped) return;
    const weight = queue.shift();
    if (weight === undefined) {
      probe.remove();
      return;
    }
    probe.style.fontWeight = String(weight);
    probe.getBoundingClientRect();
    handle = idle(step, { timeout: IDLE_TIMEOUT_MS });
  };
  handle = idle(step, { timeout: IDLE_TIMEOUT_MS });
  return () => {
    stopped = true;
    if (handle) cancelIdle(handle);
    probe.remove();
  };
}

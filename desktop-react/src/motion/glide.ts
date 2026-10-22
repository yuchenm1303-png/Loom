import { EASE, travelMs } from "./tokens";

export type GlideOptions = {
  /** How much longer the mark grows along its way through the fast middle of the trip (.1 = 10%). */
  stretch?: number;
  /** The scale the mark starts at, for a mark that leaves from a place of another size (a row of another height). */
  from?: [number, number];
};

/**
 * Carry a text-free mark (a selection pill, a highlight, a rail) from where it was painted to where layout has just
 * put it. `dx`/`dy` is the old position minus the new one.
 *
 * The mark rides `translate` on the glide curve, for a time that grows with the distance (`travelMs`: a hop takes
 * the flow step, a long way a little longer, never longer than the panel step), and stretches along its way through
 * the fast middle of the trip on `scale`. Both are compositor-only and leave the element's own `transform` alone.
 * Cancel the returned animations to stop; to turn a glide around, read the mark's painted rect before cancelling
 * and glide again from there (that is what makes a second click start where the eye already is).
 */
export function glide(mark: HTMLElement, dx: number, dy: number, options: GlideOptions = {}): Animation[] {
  const distance = Math.hypot(dx, dy);
  if (distance < 1 || typeof mark.animate !== "function") return [];
  const duration = travelMs(distance);
  const stretch = options.stretch ?? Math.min(.15, distance / 1200);
  const [fromX, fromY] = options.from ?? [1, 1];
  const middle = Math.abs(dy) >= Math.abs(dx) ? `1 ${1 + stretch}` : `${1 + stretch} 1`;
  return [
    mark.animate({ translate: [`${dx}px ${dy}px`, "0px 0px"] }, { duration, easing: EASE.glide }),
    mark.animate({ scale: [`${fromX} ${fromY}`, middle, "1 1"], offset: [0, .35, 1] }, { duration, easing: EASE.inout }),
  ];
}

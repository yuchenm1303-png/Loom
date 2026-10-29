export type SpringValue = { value: number; velocity: number; target: number };

export function stepSpring(spring: SpringValue, dt: number, stiffness: number, damping: number) {
  // Substeps keep the same spring stable at both 30 Hz and 144 Hz.
  const steps = Math.max(1, Math.ceil(dt / (1 / 120)));
  const step = dt / steps;
  for (let index = 0; index < steps; index++) {
    spring.velocity += (spring.target - spring.value) * stiffness * step;
    spring.velocity *= Math.exp(-damping * step);
    spring.value += spring.velocity * step;
  }
}

// Snappy's free follower / pinned anchor / snap weight arrangement, adapted
// to Loom's event-driven loop. The anchor remains fixed during release while
// the free follower can already track a new pointer position.
export function blendAnchor(free: number, pinned: number, weight: number) {
  return free + (pinned - free) * Math.max(0, Math.min(1, weight));
}

// How far a control leans toward the pointer, in px. Every lens stop is a magnet,
// but they do not all lean the same: a brand mark and a button can take a visible
// 7-10px, while a whole typing surface would pull the caret out from under the
// pointer if it moved that far.
export function magnetAmplitude(input: {
  brand?: boolean;
  typingSurface?: boolean;
  width: number;
  height: number;
  active?: boolean;
}) {
  if (input.brand) return 8;
  if (input.typingSurface) return 3;
  if (input.height > 64 || input.width > 520) return 4;
  return input.active ? 7 : 10;
}


/**
 * Keep independently spring-driven neighboring controls from pulling their
 * borders through each other. Only the inward part of the horizontal motion
 * is constrained, so each control still wobbles freely away from its neighbor
 * and retains the full vertical spring.
 *
 * originalGap is the gap between the controls before their CSS translations.
 */
export function constrainAdjacentMagneticOffsets(
  leftX: number,
  rightX: number,
  originalGap: number,
  minimumGap = 6,
) {
  if (!Number.isFinite(originalGap) || originalGap <= 0) return { leftX, rightX };
  const allowedClosure = Math.max(0, originalGap - Math.min(minimumGap, originalGap));
  const closure = leftX - rightX;
  if (closure <= allowedClosure) return { leftX, rightX };

  const excess = closure - allowedClosure;
  const leftInward = Math.max(0, leftX);
  const rightInward = Math.max(0, -rightX);
  const totalInward = leftInward + rightInward;
  // Give the correction to whichever neighbor was moving into the gap.
  // When both are moving inward, share it proportionally to their motion.
  const leftCorrection = totalInward > 0 ? excess * leftInward / totalInward : excess / 2;
  const rightCorrection = excess - leftCorrection;
  return {
    leftX: Math.floor((leftX - leftCorrection) * 2) / 2,
    rightX: Math.ceil((rightX + rightCorrection) * 2) / 2,
  };
}

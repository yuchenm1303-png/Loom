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

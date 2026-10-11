// Prints a CSS linear() easing for a damped spring settling inside `settle` seconds.
//   node scripts/motion-lab/icon-spring.mjs [zeta=.6] [settle seconds=.34] [samples=26] [envelope=300]
const zeta = Number(process.argv[2] || 0.6);
const settle = Number(process.argv[3] || 0.34);
const samples = Number(process.argv[4] || 26);
const w0 = Math.log(Number(process.argv[5] || 300)) / (zeta * settle);
const wd = w0 * Math.sqrt(1 - zeta * zeta);
const x = (t) => 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + (zeta / Math.sqrt(1 - zeta * zeta)) * Math.sin(wd * t));
const pts = [];
for (let i = 0; i <= samples; i++) {
  const p = i / samples;
  pts.push([x(p * settle), p]);
}
const peak = Math.max(...pts.map((p) => p[0]));
const stops = pts.map(([v, p], i) => {
  const val = Number(v.toFixed(3));
  if (i === 0) return "0";
  if (i === samples) return "1";
  return `${val} ${(p * 100).toFixed(1).replace(/\.0$/, "")}%`;
});
console.log(`/* zeta ${zeta}, settles in ${settle}s, overshoot ${(100 * (peak - 1)).toFixed(1)}% */`);
console.log(`linear(${stops.join(", ")})`);

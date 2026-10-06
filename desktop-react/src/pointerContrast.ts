// Switch native resources at surface boundaries, never move a DOM cursor.
const cursorAssets = Object.entries(import.meta.glob<string>("./assets/yukino-{cursor,pointer}*.png", {
  eager: true,
  import: "default",
})).map(([path, src]) => ({ src, scale: Number(/@([\d.]+)x\.png$/.exec(path)?.[1] ?? 1) }));
const cursorScales = [...new Set(cursorAssets.map((asset) => asset.scale))].sort((a, b) => a - b);
const preloaded = new Map<string, HTMLImageElement>();
let resolutionQuery: MediaQueryList | undefined;

// Warm both tones at the resolution image-set() picks for this display (the
// lowest one >= devicePixelRatio, else the largest) so a tone switch never
// flashes the system arrow while a cursor image loads.
function preloadForResolution() {
  const ratio = window.devicePixelRatio || 1;
  const scale = cursorScales.find((value) => value >= ratio) ?? cursorScales[cursorScales.length - 1];
  for (const asset of cursorAssets) {
    if (asset.scale !== scale || preloaded.has(asset.src)) continue;
    const image = new Image();
    image.src = asset.src;
    preloaded.set(asset.src, image);
  }
  // Moving to another monitor or zooming changes the chosen resolution.
  resolutionQuery?.removeEventListener("change", preloadForResolution);
  resolutionQuery = window.matchMedia?.(`(resolution: ${ratio}dppx)`);
  resolutionQuery?.addEventListener("change", preloadForResolution);
}
preloadForResolution();
let target: Element | null = null;
let frame = 0;
let themeTimer = 0;

function update() {
  frame = 0;
  let opacity = 0;
  let luminance = 0;
  for (let element = target; element && opacity < 0.99; element = element.parentElement) {
    const values = getComputedStyle(element).backgroundColor.match(/[\d.]+/g)?.map(Number);
    if (!values || values.length < 3) continue;
    const weight = (1 - opacity) * (values[3] ?? 1);
    luminance += weight * (0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]) / 255;
    opacity += weight;
  }
  const root = document.documentElement;
  // Images/gradients use the underlying solid background and theme baseline.
  luminance += (1 - opacity) * (root.dataset.loomTheme === "light" ? 1 : 0);
  const tone = luminance < 0.5 ? "white" : "black";
  if (root.dataset.loomPointerTone !== tone) root.dataset.loomPointerTone = tone;
}
function schedule() {
  if (!frame) frame = requestAnimationFrame(update);
}
function onPointerOver(event: PointerEvent) {
  if (event.pointerType === "touch") return;
  target = event.target instanceof Element ? event.target : null;
  schedule();
}
function onThemeChanged() {
  // Use the new theme immediately, then inspect the surface after its color transition.
  delete document.documentElement.dataset.loomPointerTone;
  cancelAnimationFrame(frame);
  frame = 0;
  clearTimeout(themeTimer);
  themeTimer = window.setTimeout(schedule, 300);
}
document.addEventListener("pointerover", onPointerOver, { passive: true });
window.addEventListener("loom-theme-changed", onThemeChanged);
if (import.meta.hot) import.meta.hot.dispose(() => {
  document.removeEventListener("pointerover", onPointerOver);
  window.removeEventListener("loom-theme-changed", onThemeChanged);
  cancelAnimationFrame(frame);
  clearTimeout(themeTimer);
  delete document.documentElement.dataset.loomPointerTone;
  resolutionQuery?.removeEventListener("change", preloadForResolution);
  preloaded.clear();
});

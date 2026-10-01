import blackCursor from "./assets/yukino-cursor.png";
import whiteCursor from "./assets/yukino-cursor-white.png";

// Switch native resources at surface boundaries, never move a DOM cursor.
const preloaded = [blackCursor, whiteCursor].map((src) => {
  const image = new Image();
  image.src = src;
  return image;
});
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
  preloaded.length = 0;
});

import "./pointer-motion.css";

// Native frames keep the OS-controlled hotspot at (0, 0). No following DOM
// layer, positional easing, or character transforms are involved.
const assets = Object.entries(import.meta.glob<string>("./assets/cursor-motion/*.png", {
  eager: true, import: "default",
})).map(([path, src]) => ({ src, scale: Number(/@([\d.]+)x\.png$/.exec(path)?.[1] ?? 1) }));
const scales = [1, 1.5, 2, 3];
const root = document.documentElement;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const finePointer = matchMedia("(any-pointer: fine)");
const images = new Map<string, HTMLImageElement>();
let resolution: MediaQueryList | undefined;
let ready = false;
let disposed = false;
let generation = 0;
let raf = 0;
let inside = false;
let hover = false;
let pressed = false;
let size = 1;
let lastTick = 0;

function allowed() {
  return ready && inside && !document.hidden && finePointer.matches
    && !reducedMotion.matches && root.dataset.loomReducedMotion !== "true"
    && !root.classList.contains("loom-liquid-cursor-active");
}
function setFrame(name = "") {
  if (name) {
    if (root.dataset.loomCursorFrame !== name) root.dataset.loomCursorFrame = name;
  } else if (root.hasAttribute("data-loom-cursor-frame")) {
    delete root.dataset.loomCursorFrame;
  }
}
function reset() {
  cancelAnimationFrame(raf);
  raf = 0;
  size = 1;
  lastTick = 0;
  pressed = false;
  setFrame();
}
function tick(now: number) {
  raf = 0;
  if (!allowed()) { reset(); return; }
  const target = pressed ? .92 : hover ? 1.04 : 1;
  const dt = lastTick ? Math.min(32, now - lastTick) : 16;
  lastTick = now;
  // Monotonic easing: press in ~90ms, hover in ~120ms, release in ~180ms.
  // No directional warping, overshoot, positional lag or idle movement.
  const duration = pressed ? 26 : target > size ? 44 : 36;
  size += (target - size) * (1 - Math.exp(-dt / duration));
  if (Math.abs(size - target) < .0007) size = target;
  const step = Math.max(2, Math.min(14, Math.round((size - .9) * 100)));
  setFrame(step === 10 ? "" : `scale-${step}`);
  if (size !== target) raf = requestAnimationFrame(tick);
  else lastTick = 0;
}
function wake() {
  if (!raf) raf = requestAnimationFrame(tick);
}
async function preload() {
  const id = ++generation;
  ready = false;
  reset();
  const ratio = devicePixelRatio || 1;
  const scale = scales.find(value => value >= ratio) ?? 3;
  const selected = assets.filter(asset => asset.scale === scale);
  try {
    await Promise.all(selected.map(async asset => {
      let image = images.get(asset.src);
      if (!image) { image = new Image(); image.src = asset.src; images.set(asset.src, image); }
      await image.decode();
    }));
    if (!disposed && id === generation) { ready = true; wake(); }
  } catch {
    // Keep the static native cursor if any animation bitmap cannot be decoded.
  }
  if (disposed || id !== generation) return;
  resolution?.removeEventListener("change", preload);
  resolution = matchMedia(`(resolution: ${ratio}dppx)`);
  resolution.addEventListener("change", preload);
}
function onMove(event: PointerEvent) {
  if ((event.pointerType && event.pointerType !== "mouse") || !event.isPrimary) return;
  inside = true;
  wake();
}
function onOver(event: PointerEvent) {
  if (event.pointerType && event.pointerType !== "mouse") return;
  inside = true;
  const target = event.target instanceof Element ? event.target : null;
  const control = target?.closest("button, a[href], [role='button'], summary, label, select, [data-magnetic-hover='true']");
  hover = !!control && !control.matches(":disabled, [aria-disabled='true'], [inert], [inert] *");
  wake();
}
function onDown(event: PointerEvent) {
  if ((event.pointerType && event.pointerType !== "mouse") || !event.isPrimary || event.button !== 0) return;
  inside = true;
  pressed = true;
  wake();
}
function onUp(event: PointerEvent) {
  if ((event.pointerType && event.pointerType !== "mouse") || event.button !== 0 || !pressed) return;
  pressed = false;
  wake();
}
function onLeave(event: PointerEvent) {
  if (event.relatedTarget !== null) return;
  onBlur();
}
function onBlur() { inside = false; hover = false; reset(); }
function onCancel() { reset(); wake(); }
function onPreferences() { reset(); wake(); }
const observer = new MutationObserver(onPreferences);
observer.observe(root, { attributes: true, attributeFilter: ["data-loom-reduced-motion", "class"] });
document.addEventListener("pointermove", onMove, { passive: true });
document.addEventListener("pointerover", onOver, { passive: true });
document.addEventListener("pointerout", onLeave, { passive: true });
document.addEventListener("pointerdown", onDown, { capture: true, passive: true });
window.addEventListener("pointerup", onUp, { capture: true, passive: true });
window.addEventListener("pointercancel", onCancel);
window.addEventListener("blur", onBlur);
document.addEventListener("visibilitychange", onBlur);
reducedMotion.addEventListener("change", onPreferences);
finePointer.addEventListener("change", onPreferences);
void preload();

if (import.meta.hot) import.meta.hot.dispose(() => {
  disposed = true;
  reset();
  observer.disconnect();
  resolution?.removeEventListener("change", preload);
  reducedMotion.removeEventListener("change", onPreferences);
  finePointer.removeEventListener("change", onPreferences);
  document.removeEventListener("pointermove", onMove);
  document.removeEventListener("pointerover", onOver);
  document.removeEventListener("pointerout", onLeave);
  document.removeEventListener("pointerdown", onDown, true);
  window.removeEventListener("pointerup", onUp, true);
  window.removeEventListener("pointercancel", onCancel);
  window.removeEventListener("blur", onBlur);
  document.removeEventListener("visibilitychange", onBlur);
  images.clear();
});

import source from "./assets/yukino-mouse.png";
import "./custom-pointer.css";

// Native cursor images may fall back when their bitmap crosses the viewport.
// Paint the original bitmap in the client area with its original (0, 0) hotspot.
const layer = document.createElement("div");
layer.className = "loom-custom-pointer-layer";
layer.setAttribute("aria-hidden", "true");
const image = document.createElement("img");
image.className = "loom-custom-pointer-image";
image.alt = "";
image.draggable = false;
image.hidden = true;
layer.append(image);
document.body.append(layer);
let ready = false;
let inside = false;
let x = 0, y = 0;

function hide() {
  inside = false;
  image.hidden = true;
  if (document.documentElement.dataset.loomCustomPointer) delete document.documentElement.dataset.loomCustomPointer;
}
function activate() {
  if (document.documentElement.dataset.loomCustomPointer !== "true") document.documentElement.dataset.loomCustomPointer = "true";
}
function syncScale() {
  const scale = Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const zoom = String(1 / scale);
  if (layer.style.zoom !== zoom) layer.style.zoom = zoom;
}
const scaleObserver = new MutationObserver(syncScale);
scaleObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
syncScale();
function draw(event: PointerEvent) {
  if (event.pointerType === "touch" || !event.isPrimary) { hide(); return; }
  x = event.clientX; y = event.clientY;
  inside = x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
  if (!ready || !inside || document.hidden) { hide(); return; }
  // No easing, React updates or layout reads: the arrow must track clicks exactly.
  image.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  image.hidden = false;
  activate();
}
image.onload = () => {
  ready = true;
  if (inside) {
    image.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    image.hidden = false;
    activate();
  }
};
image.onerror = hide;
image.src = source;
document.addEventListener("pointermove", draw, { capture: true, passive: true });
document.addEventListener("pointerover", draw, { capture: true, passive: true });
document.addEventListener("pointerdown", draw, { capture: true, passive: true });
document.documentElement.addEventListener("pointerleave", hide);
document.addEventListener("visibilitychange", hide);
window.addEventListener("blur", hide);
if (import.meta.hot) import.meta.hot.dispose(() => {
  hide(); layer.remove(); image.onload = null; image.onerror = null;
  scaleObserver.disconnect();
  document.removeEventListener("pointermove", draw, true);
  document.removeEventListener("pointerover", draw, true);
  document.removeEventListener("pointerdown", draw, true);
  document.documentElement.removeEventListener("pointerleave", hide);
  document.removeEventListener("visibilitychange", hide);
  window.removeEventListener("blur", hide);
});

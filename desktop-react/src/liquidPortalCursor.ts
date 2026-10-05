import "./liquid-portal-cursor.css";

const ROOT_FLAG = "loomLiquidPortalCursor";
const PORTAL_SELECTOR = ".loom-portal-page";
const SNAP_SELECTOR = "button, a, [role='button'], [data-loom-liquid-snap='true']";
const DEFAULT_WIDTH = 80;
const DEFAULT_HEIGHT = 54;

let layer: HTMLDivElement | null = null;
let cursor: HTMLDivElement | null = null;
let animationFrame = 0;
let lastFrame = performance.now();
let targetX = -9999;
let targetY = -9999;
let targetWidth = DEFAULT_WIDTH;
let targetHeight = DEFAULT_HEIGHT;
let targetRadius = DEFAULT_HEIGHT / 2;
let x = targetX;
let y = targetY;
let width = targetWidth;
let height = targetHeight;
let radius = targetRadius;
let vx = 0;
let vy = 0;
let vw = 0;
let vh = 0;
let vr = 0;
let visible = false;
let snapped = false;
let pressed = false;

function motionReduced(): boolean {
  return document.documentElement.dataset.loomReducedMotion === "true"
    || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

function canUseLiquidCursor(): boolean {
  return !motionReduced()
    && Boolean(window.matchMedia?.("(hover: hover) and (pointer: fine)").matches);
}

function ensureLayer(): HTMLDivElement {
  if (layer) return layer;

  layer = document.createElement("div");
  layer.className = "loom-liquid-portal-cursor-layer";
  layer.setAttribute("aria-hidden", "true");
  layer.innerHTML = `
    <svg class="loom-liquid-portal-filter" width="0" height="0" aria-hidden="true">
      <defs>
        <filter id="loom-liquid-portal-refraction" x="-60%" y="-60%" width="220%" height="220%" color-interpolation-filters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.012 0.018" numOctaves="2" seed="17" result="noise" />
          <feGaussianBlur in="noise" stdDeviation="0.55" result="softNoise" />
          <feDisplacementMap in="SourceGraphic" in2="softNoise" scale="11" xChannelSelector="R" yChannelSelector="G" result="warped" />
          <feColorMatrix in="warped" type="matrix" values="1.035 0 0 0 0  0 1.015 0 0 0  0 0 1.06 0 0  0 0 0 1 0" />
        </filter>
      </defs>
    </svg>
    <div class="loom-liquid-portal-cursor" data-snapped="false" data-pressed="false">
      <div class="loom-liquid-portal-cursor__surface"></div>
      <div class="loom-liquid-portal-cursor__rim"></div>
      <div class="loom-liquid-portal-cursor__focus"></div>
    </div>
  `;
  document.body.append(layer);
  cursor = layer.querySelector<HTMLDivElement>(".loom-liquid-portal-cursor");
  return layer;
}

function setEnabled(enabled: boolean): void {
  const root = document.documentElement;
  if (enabled) root.dataset[ROOT_FLAG] = "true";
  else delete root.dataset[ROOT_FLAG];
  if (!enabled) setVisible(false);
}

function setVisible(next: boolean): void {
  if (visible === next) return;
  visible = next;
  ensureLayer();
  cursor?.classList.toggle("is-visible", next);
  if (next) {
    lastFrame = performance.now();
    if (!animationFrame) animationFrame = requestAnimationFrame(animate);
  } else if (animationFrame) {
    cancelAnimationFrame(animationFrame);
    animationFrame = 0;
  }
}

function springStep(current: number, velocity: number, target: number, dt: number, stiffness: number, damping: number): [number, number] {
  const acceleration = (target - current) * stiffness - velocity * damping;
  const nextVelocity = velocity + acceleration * dt;
  const next = current + nextVelocity * dt;
  return [next, nextVelocity];
}

function animate(now: number): void {
  animationFrame = 0;
  const dt = Math.min(0.032, Math.max(0.001, (now - lastFrame) / 1000));
  lastFrame = now;

  if (!visible || !cursor) return;
  if (!document.querySelector(PORTAL_SELECTOR)) {
    setVisible(false);
    return;
  }

  [x, vx] = springStep(x, vx, targetX, dt, snapped ? 360 : 500, snapped ? 38 : 52);
  [y, vy] = springStep(y, vy, targetY, dt, snapped ? 360 : 500, snapped ? 38 : 52);
  [width, vw] = springStep(width, vw, targetWidth, dt, 330, 34);
  [height, vh] = springStep(height, vh, targetHeight, dt, 330, 34);
  [radius, vr] = springStep(radius, vr, targetRadius, dt, 330, 34);

  const speed = Math.min(1, Math.hypot(vx, vy) / 1400);
  const angle = Math.atan2(vy, vx) * (180 / Math.PI);
  const stretch = 1 + speed * 0.055;
  const squash = 1 - speed * 0.025;

  cursor.style.width = `${Math.max(1, width)}px`;
  cursor.style.height = `${Math.max(1, height)}px`;
  cursor.style.borderRadius = `${Math.max(8, radius)}px`;
  cursor.style.transform = `translate3d(${x - width / 2}px, ${y - height / 2}px, 0) rotate(${angle * speed * 0.018}deg) scale(${stretch}, ${squash})`;
  cursor.style.setProperty("--loom-liquid-speed", speed.toFixed(3));
  cursor.style.setProperty("--loom-liquid-angle", `${angle}deg`);
  cursor.style.setProperty("--loom-liquid-light-x", `${50 + Math.cos(angle * Math.PI / 180) * 18}%`);
  cursor.style.setProperty("--loom-liquid-light-y", `${50 + Math.sin(angle * Math.PI / 180) * 18}%`);
  if (visible) animationFrame = requestAnimationFrame(animate);
}

function resolveSnapTarget(target: Element | null): HTMLElement | null {
  const candidate = target?.closest<HTMLElement>(SNAP_SELECTOR) ?? null;
  if (!candidate || candidate.matches(":disabled") || candidate.getAttribute("aria-disabled") === "true") return null;
  if (!candidate.closest(PORTAL_SELECTOR) || !document.querySelector(PORTAL_SELECTOR)) return null;
  const rect = candidate.getBoundingClientRect();
  if (rect.width > 380 || rect.height > 126 || rect.width < 12 || rect.height < 12) return null;
  return candidate;
}

function applyPointer(event: PointerEvent): void {
  if (event.pointerType === "touch" || !canUseLiquidCursor()) {
    setEnabled(false);
    return;
  }
  setEnabled(true);

  const element = event.target instanceof Element ? event.target : null;
  const portal = document.querySelector<HTMLElement>(PORTAL_SELECTOR);
  const portalTarget = element?.closest<HTMLElement>(PORTAL_SELECTOR) ?? null;
  if (!portal || !portalTarget) {
    setVisible(false);
    return;
  }

  ensureLayer();
  if (!visible) {
    x = targetX = event.clientX;
    y = targetY = event.clientY;
    width = targetWidth = DEFAULT_WIDTH;
    height = targetHeight = DEFAULT_HEIGHT;
    radius = targetRadius = DEFAULT_HEIGHT / 2;
    vx = vy = vw = vh = vr = 0;
  }
  setVisible(true);

  const snap = resolveSnapTarget(element);
  if (snap) {
    const rect = snap.getBoundingClientRect();
    const style = getComputedStyle(snap);
    const snapPadding = snap.classList.contains("starter-card") ? 10 : 8;
    targetX = rect.left + rect.width / 2;
    targetY = rect.top + rect.height / 2;
    targetWidth = Math.min(380, Math.max(58, rect.width + snapPadding * 2));
    targetHeight = Math.min(126, Math.max(48, rect.height + snapPadding * 2));
    const parsedRadius = Number.parseFloat(style.borderTopLeftRadius) || 16;
    targetRadius = Math.min(targetHeight / 2, parsedRadius + snapPadding);
    snapped = true;
  } else {
    targetX = event.clientX;
    targetY = event.clientY;
    targetWidth = DEFAULT_WIDTH;
    targetHeight = DEFAULT_HEIGHT;
    targetRadius = DEFAULT_HEIGHT / 2;
    snapped = false;
  }

  if (cursor) cursor.dataset.snapped = String(snapped);
}

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0 || !visible) return;
  pressed = true;
  if (cursor) cursor.dataset.pressed = "true";
}

function onPointerUp(): void {
  if (!pressed) return;
  pressed = false;
  if (cursor) cursor.dataset.pressed = "false";
}

function onLeave(): void {
  setVisible(false);
  onPointerUp();
}

function refreshCapability(): void {
  setEnabled(canUseLiquidCursor());
}

const pointerMedia = window.matchMedia?.("(hover: hover) and (pointer: fine)");
const reduceMedia = window.matchMedia?.("(prefers-reduced-motion: reduce)");
const rootObserver = new MutationObserver(refreshCapability);
rootObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-reduced-motion"] });
pointerMedia?.addEventListener("change", refreshCapability);
reduceMedia?.addEventListener("change", refreshCapability);
document.addEventListener("pointermove", applyPointer, { passive: true });
document.addEventListener("pointerdown", onPointerDown, { capture: true, passive: true });
document.addEventListener("pointerup", onPointerUp, { capture: true, passive: true });
document.addEventListener("pointercancel", onPointerUp, { capture: true, passive: true });
document.addEventListener("visibilitychange", () => document.hidden && onLeave());
window.addEventListener("blur", onLeave);
document.documentElement.addEventListener("mouseleave", onLeave);

refreshCapability();
ensureLayer();

if (import.meta.hot) import.meta.hot.dispose(() => {
  cancelAnimationFrame(animationFrame);
  rootObserver.disconnect();
  pointerMedia?.removeEventListener("change", refreshCapability);
  reduceMedia?.removeEventListener("change", refreshCapability);
  document.removeEventListener("pointermove", applyPointer);
  document.removeEventListener("pointerdown", onPointerDown, true);
  document.removeEventListener("pointerup", onPointerUp, true);
  document.removeEventListener("pointercancel", onPointerUp, true);
  window.removeEventListener("blur", onLeave);
  document.documentElement.removeEventListener("mouseleave", onLeave);
  layer?.remove();
  layer = null;
  cursor = null;
  delete document.documentElement.dataset[ROOT_FLAG];
});
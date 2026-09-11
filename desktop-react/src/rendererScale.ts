const MIN_ZOOM = 0.9;
const MAX_ZOOM = 1.3;

function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function parseZoom(value: string | number): number {
  const numeric = typeof value === "number" ? value : Number.parseFloat(String(value || "1"));
  return clampZoom(Number.isFinite(numeric) ? numeric : 1);
}

/**
 * Apply interface scale through Chromium's native page zoom whenever Electron
 * is available. This keeps text/vector UI on the browser's normal raster path
 * instead of scaling a pre-rasterized CSS surface.
 */
export function applyRendererScale(value: string | number): number {
  const factor = parseZoom(value);
  const root = document.documentElement;
  const bridge = window.loom;

  if (bridge?.setZoomFactor) {
    try {
      const applied = bridge.setZoomFactor(factor);
      root.dataset.loomScale = String(Math.round(applied * 100));
      root.style.removeProperty("zoom");
      return applied;
    } catch {
      // Browser/dev fallback below.
    }
  }

  root.style.setProperty("zoom", String(factor));
  root.dataset.loomScale = String(Math.round(factor * 100));
  return factor;
}

function applyNativeZoomFromCss(): boolean {
  const root = document.documentElement;
  const cssZoom = root.style.getPropertyValue("zoom");
  if (!cssZoom || !window.loom?.setZoomFactor) return false;
  applyRendererScale(cssZoom);
  return !root.style.getPropertyValue("zoom");
}

/**
 * SettingsPage still writes the legacy CSS zoom property. Consume that write
 * before the next render and convert it to native page zoom, while initial
 * startup can call applyRendererScale directly and never touch CSS zoom.
 */
export function installNativeRendererScaleSync(): () => void {
  const root = document.documentElement;
  if (root.style.getPropertyValue("zoom")) applyNativeZoomFromCss();

  const observer = new MutationObserver(() => {
    if (!root.style.getPropertyValue("zoom")) return;
    applyNativeZoomFromCss();
  });
  observer.observe(root, { attributes: true, attributeFilter: ["style"] });
  return () => observer.disconnect();
}

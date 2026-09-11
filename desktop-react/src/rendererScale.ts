const MIN_ZOOM = 0.9;
const MAX_ZOOM = 1.3;

function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function parseCssZoom(value: string): number {
  const numeric = Number.parseFloat(String(value || "1"));
  return clampZoom(Number.isFinite(numeric) ? numeric : 1);
}

function applyNativeZoomFromCss(): boolean {
  const root = document.documentElement;
  const cssZoom = root.style.zoom;
  if (!cssZoom) return false;

  const bridge = window.loom;
  if (!bridge?.setZoomFactor) return false;

  try {
    const factor = bridge.setZoomFactor(parseCssZoom(cssZoom));
    root.dataset.loomScale = String(Math.round(factor * 100));
    // CSS zoom rasterizes the complete renderer surface and is noticeably
    // softer on Windows at non-100% DPI. Native page zoom keeps Chromium's text
    // and 1px geometry on the device-pixel rendering path instead.
    root.style.removeProperty("zoom");
    return true;
  } catch {
    // Browser/dev previews may not expose the Electron bridge. In that case the
    // existing CSS zoom remains as a compatibility fallback.
    return false;
  }
}

/**
 * Converts the existing Appearance scale setting from CSS zoom to Electron's
 * native page zoom. SettingsPage still writes the legacy CSS property; this
 * observer consumes it immediately so old settings code and persisted values
 * keep working without rendering the application through a scaled bitmap.
 */
export function installNativeRendererScaleSync(): () => void {
  const root = document.documentElement;
  applyNativeZoomFromCss();

  const observer = new MutationObserver(() => {
    if (!root.style.zoom) return;
    applyNativeZoomFromCss();
  });
  observer.observe(root, { attributes: true, attributeFilter: ["style"] });
  return () => observer.disconnect();
}

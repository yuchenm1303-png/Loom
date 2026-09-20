export type LoomThemePreference = "system" | "light" | "dark";
export type LoomResolvedTheme = "light" | "dark";

const SETTINGS_STORAGE_KEY = "loom.settings.desktop.v2";
const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";
let transitionTimer: number | null = null;
let systemListenerInstalled = false;

function normalizeTheme(value: unknown): LoomThemePreference {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

function readPersistedTheme(): LoomThemePreference {
  try {
    const stored = JSON.parse(window.localStorage.getItem(SETTINGS_STORAGE_KEY) || "{}");
    return normalizeTheme(stored?.appearance?.theme);
  } catch {
    return "system";
  }
}

function resolveTheme(preference: LoomThemePreference): LoomResolvedTheme {
  if (preference === "light" || preference === "dark") return preference;
  return window.matchMedia?.(SYSTEM_DARK_QUERY).matches ? "dark" : "light";
}

function syncNativeTheme(preference: LoomThemePreference): void {
  try {
    if (window.loom?.setNativeTheme) {
      void window.loom.setNativeTheme(preference).catch(() => undefined);
    }
  } catch {
    // Renderer theming remains active even if native chrome cannot be updated.
  }
}

function markThemeTransition(changed: boolean, animate: boolean): void {
  const root = document.documentElement;
  if (transitionTimer !== null) {
    window.clearTimeout(transitionTimer);
    transitionTimer = null;
  }
  root.classList.remove("loom-theme-transitioning");
  if (!changed || !animate || root.dataset.loomReducedMotion === "true") return;
  void root.offsetWidth;
  root.classList.add("loom-theme-transitioning");
  transitionTimer = window.setTimeout(() => {
    root.classList.remove("loom-theme-transitioning");
    transitionTimer = null;
  }, 260);
}

export function applyThemePreference(
  value: LoomThemePreference,
  options: { animate?: boolean; syncNative?: boolean } = {},
): LoomResolvedTheme {
  const preference = normalizeTheme(value);
  const resolved = resolveTheme(preference);
  const root = document.documentElement;
  const previous = root.dataset.loomTheme as LoomResolvedTheme | undefined;
  const changed = previous !== undefined && previous !== resolved;

  root.dataset.loomThemePreference = preference;
  root.dataset.loomTheme = resolved;
  root.dataset.loomThemeReady = "true";
  root.style.colorScheme = resolved;
  markThemeTransition(changed, options.animate !== false);

  if (options.syncNative !== false) syncNativeTheme(preference);
  window.dispatchEvent(new CustomEvent("loom-theme-changed", {
    detail: { preference, resolved },
  }));
  return resolved;
}

function installSystemThemeListener(): void {
  if (systemListenerInstalled || typeof window.matchMedia !== "function") return;
  systemListenerInstalled = true;
  const media = window.matchMedia(SYSTEM_DARK_QUERY);
  media.addEventListener("change", () => {
    if (document.documentElement.dataset.loomThemePreference !== "system") return;
    applyThemePreference("system", { animate: true, syncNative: false });
  });
}

export function bootstrapTheme(): LoomResolvedTheme {
  installSystemThemeListener();
  return applyThemePreference(readPersistedTheme(), { animate: false, syncNative: true });
}

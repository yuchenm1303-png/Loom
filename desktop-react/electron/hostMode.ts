export const BACKGROUND_HOST_ARG = "--loom-background-host";

// Host runtime version is intentionally independent from the Desktop package
// version. The browser cares about Host protocol/runtime compatibility; the
// Electron UI may evolve on a different cadence.
export const LOOM_HOST_RUNTIME_VERSION = "1.0.0";

export type LoomHostLaunchMode = "background" | "desktop";

let runtimeModeOverride: LoomHostLaunchMode | null = null;

export function setLoomHostLaunchMode(mode: LoomHostLaunchMode | null): void {
  runtimeModeOverride = mode;
}

export function isBackgroundHostLaunch(argv: readonly string[] = process.argv): boolean {
  if (runtimeModeOverride) return runtimeModeOverride === "background";
  return argv.includes(BACKGROUND_HOST_ARG);
}

export function loomHostLaunchMode(argv: readonly string[] = process.argv): LoomHostLaunchMode {
  if (runtimeModeOverride) return runtimeModeOverride;
  return argv.includes(BACKGROUND_HOST_ARG) ? "background" : "desktop";
}

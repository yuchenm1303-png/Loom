export const BACKGROUND_HOST_ARG = "--loom-background-host";

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

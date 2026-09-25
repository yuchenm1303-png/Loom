import { useSyncExternalStore } from "react";

export type NativeSettingsRoute =
  | "general"
  | "appearance"
  | "models"
  | "capabilities"
  | "computer"
  | "browser"
  | "websearch"
  | "terminal"
  | "plugins"
  | "mcp"
  | "skills"
  | "permissions"
  | "shortcuts"
  | "privacy"
  | "developer";

export type SettingsRoute = NativeSettingsRoute | "memory" | "connectors";

let currentRoute: SettingsRoute = "general";
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): SettingsRoute {
  return currentRoute;
}

export function setSettingsRoute(route: SettingsRoute): void {
  if (route === currentRoute) return;
  currentRoute = route;
  for (const listener of listeners) listener();
}

export function useSettingsRoute(): SettingsRoute {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

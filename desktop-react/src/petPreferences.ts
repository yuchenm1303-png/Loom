import { useSyncExternalStore } from "react";

export const DESKTOP_SETTINGS_STORAGE_KEY = "loom.settings.desktop.v2";
export const DEFAULT_PET_ENABLED = false;
const PET_CHANGED_EVENT = "loom:pet-visibility-changed";
let sessionPreference: boolean | undefined;

function readPersistedPreference(): boolean {
  try {
    const settings = JSON.parse(window.localStorage.getItem(DESKTOP_SETTINGS_STORAGE_KEY) || "{}");
    return settings?.appearance?.petEnabled === true;
  } catch {
    return DEFAULT_PET_ENABLED;
  }
}

function snapshot(): boolean {
  return sessionPreference ?? readPersistedPreference();
}

/** Settings owns persistence; this also works when local storage is unavailable. */
export function applyPetVisibility(enabled: unknown): void {
  sessionPreference = enabled === true;
  window.dispatchEvent(new Event(PET_CHANGED_EVENT));
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== DESKTOP_SETTINGS_STORAGE_KEY && event.key !== null) return;
    sessionPreference = readPersistedPreference();
    onChange();
  };
  window.addEventListener(PET_CHANGED_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PET_CHANGED_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function usePetEnabled(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => DEFAULT_PET_ENABLED);
}

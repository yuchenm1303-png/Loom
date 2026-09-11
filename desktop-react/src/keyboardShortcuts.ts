export type ShortcutCommandId =
  | "newConversation"
  | "searchConversations"
  | "openSettings"
  | "focusComposer"
  | "toggleSidebar"
  | "toggleInspector"
  | "attachFiles"
  | "stopTask";

export type ShortcutSettings = Record<ShortcutCommandId, string>;

export const DEFAULT_SHORTCUTS: ShortcutSettings = {
  newConversation: "Ctrl+N",
  searchConversations: "Ctrl+K",
  openSettings: "Ctrl+,",
  focusComposer: "Ctrl+L",
  toggleSidebar: "Ctrl+B",
  toggleInspector: "Ctrl+Shift+I",
  attachFiles: "Ctrl+Shift+A",
  stopTask: "Escape",
};

export const SHORTCUTS_CHANGED_EVENT = "loom:shortcuts-changed";
export const FOCUS_THREAD_SEARCH_EVENT = "loom:focus-thread-search";
export const FOCUS_COMPOSER_EVENT = "loom:focus-composer";
export const ATTACH_FILES_EVENT = "loom:attach-files";

const MODIFIER_ORDER = ["Ctrl", "Alt", "Shift", "Meta"] as const;
const MODIFIER_ALIASES: Record<string, (typeof MODIFIER_ORDER)[number]> = {
  ctrl: "Ctrl",
  control: "Ctrl",
  alt: "Alt",
  option: "Alt",
  shift: "Shift",
  meta: "Meta",
  cmd: "Meta",
  command: "Meta",
};

function normalizeKeyToken(value: string): string {
  const token = value.trim();
  if (!token) return "";
  const lower = token.toLowerCase();
  if (lower === "esc" || lower === "escape") return "Escape";
  if (lower === "enter" || lower === "return") return "Enter";
  if (lower === "space" || lower === " ") return "Space";
  if (lower === "backspace") return "Backspace";
  if (lower === "delete" || lower === "del") return "Delete";
  if (lower === "tab") return "Tab";
  if (lower === "arrowup") return "ArrowUp";
  if (lower === "arrowdown") return "ArrowDown";
  if (lower === "arrowleft") return "ArrowLeft";
  if (lower === "arrowright") return "ArrowRight";
  if (/^f\d{1,2}$/i.test(token)) return token.toUpperCase();
  if (token.length === 1) return /[a-z]/i.test(token) ? token.toUpperCase() : token;
  return token;
}

export function normalizeShortcut(value: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw === ",") return ",";

  const parts = raw.split("+").map((part) => part.trim()).filter(Boolean);
  const modifiers = new Set<(typeof MODIFIER_ORDER)[number]>();
  let key = "";
  for (const part of parts) {
    const modifier = MODIFIER_ALIASES[part.toLowerCase()];
    if (modifier) {
      modifiers.add(modifier);
      continue;
    }
    key = normalizeKeyToken(part);
  }

  if (!key && raw.endsWith("+")) key = "+";
  if (!key && raw.includes(",")) key = ",";
  if (!key) return "";
  return [...MODIFIER_ORDER.filter((modifier) => modifiers.has(modifier)), key].join("+");
}

export function formatShortcut(value: string): string {
  const normalized = normalizeShortcut(value);
  return normalized ? normalized.split("+").join(" + ") : "Unassigned";
}

export function shortcutFromKeyboardEvent(event: KeyboardEvent | React.KeyboardEvent): string {
  const modifiers: string[] = [];
  if (event.ctrlKey) modifiers.push("Ctrl");
  if (event.altKey) modifiers.push("Alt");
  if (event.shiftKey) modifiers.push("Shift");
  if (event.metaKey) modifiers.push("Meta");

  const rawKey = event.key;
  const lower = rawKey.toLowerCase();
  if (["control", "shift", "alt", "meta"].includes(lower)) return "";
  const key = normalizeKeyToken(rawKey);
  if (!key) return "";
  return [...modifiers, key].join("+");
}

export function eventMatchesShortcut(event: KeyboardEvent, value: string): boolean {
  const expected = normalizeShortcut(value);
  if (!expected) return false;
  return shortcutFromKeyboardEvent(event) === expected;
}

export function mergeShortcutSettings(value?: Partial<Record<ShortcutCommandId, string>> | null): ShortcutSettings {
  const next = { ...DEFAULT_SHORTCUTS };
  if (!value) return next;
  for (const key of Object.keys(DEFAULT_SHORTCUTS) as ShortcutCommandId[]) {
    const candidate = normalizeShortcut(String(value[key] || ""));
    if (candidate) next[key] = candidate;
  }
  return next;
}

export function readShortcutSettings(): ShortcutSettings {
  try {
    const payload = JSON.parse(window.localStorage.getItem("loom.settings.desktop.v2") || "{}");
    return mergeShortcutSettings(payload?.shortcuts ?? {});
  } catch {
    return { ...DEFAULT_SHORTCUTS };
  }
}

export function findShortcutConflict(
  shortcuts: ShortcutSettings,
  command: ShortcutCommandId,
  candidate: string,
): ShortcutCommandId | null {
  const normalized = normalizeShortcut(candidate);
  if (!normalized) return null;
  for (const key of Object.keys(shortcuts) as ShortcutCommandId[]) {
    if (key !== command && normalizeShortcut(shortcuts[key]) === normalized) return key;
  }
  return null;
}

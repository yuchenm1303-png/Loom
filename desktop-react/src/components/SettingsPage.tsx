import { AmbientSettingsPanel } from "./AmbientSettingsPanel";
import { ProjectRunsPanel } from "./ProjectRunsPanel";
import { PluginsSettingsPanel } from "./PluginsSettingsPanel";
import { McpSettingsPanel } from "./McpSettingsPanel";
import { StickerPanel } from "./StickerPanel";
import { ProgressFeedbackControl } from "./ProgressFeedbackControl";
import type { ProgressFeedbackMode } from "../types/loom";
import {
  Activity,
  ArrowLeft,
  Blocks,
  Bot,
  Box,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  Copy,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  Globe2,
  Info,
  Keyboard,
  Link2,
  Monitor,
  Moon,
  Palette,
  Plug,
  RefreshCw,
  Search,
  Settings,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Sun,
  Terminal,
  Type,
  Wrench,
  type LucideIcon,
} from "./icons";
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  DEFAULT_SHORTCUTS,
  SHORTCUTS_CHANGED_EVENT,
  mergeShortcutSettings,
  type ShortcutCommandId,
  type ShortcutSettings,
} from "../keyboardShortcuts";
import { useI18n } from "../i18n";
import { isLoomWebRuntime } from "../webBridge";
import { applyThemePreference, type LoomThemePreference } from "../theme";
import { applyProgressFeedback } from "../progressFeedback";
import { applyPetVisibility, DEFAULT_PET_ENABLED, DESKTOP_SETTINGS_STORAGE_KEY } from "../petPreferences";
import { useMotionPresence } from "../motion/useMotionPresence";
import { usePaintedOnce } from "../motion/usePaintedOnce";
import { RETAIN } from "../motion/tokens";
import type {
  InitializeResult,
  LoomSettings,
  ModelSnapshot,
} from "../types/loom";
import { KeyboardShortcutsSettings } from "./KeyboardShortcutsSettings";
import { ModelsSettingsPanel } from "./ModelsSettingsPanel";
import { MemoryPanel } from "./SettingsMemoryBridge";
import { SettingsWebSearchPanel } from "./SettingsWebSearchPanel";
import { ConnectorsSettings } from "./ConnectorsSettings";
import { ConnectorLifecycleStatus } from "./ConnectorLifecycleStatus";
import { translateSettingsText } from "./LanguageSettingsDock";
import "./settings-connectors.css";
import { setSettingsRoute, useSettingsRoute } from "./settingsNavigation";
import { playPageTransition, prepareNavTravel, type PageFlow } from "./settingsMotion";
import { prefersReducedMotionNow } from "../motion/useReducedMotion";
import "./settings-page.css";
import "./settings-general-polish.css";
import "./settings-maturity.css";
import "./settings-appearance.css";
import "./settings-terminal.css";
import "./settings-page-motion.css";
import "./settings-refined.css";
import "./settings-general-home.css";
import "./settings-system.css";

type PageKey =
  | "ambient"
  | "projectRuns"
  | "general"
  | "appearance"
  | "models"
  | "capabilities"
  | "computer"
  | "browser"
  | "websearch"
  | "terminal"
  | "plugins"
  | "connectors"
  | "mcp"
  | "skills"
  | "memory"
  | "permissions"
  | "shortcuts"
  | "privacy"
  | "developer";

type SettingsNotice = { tone: "error" | "success"; text: string; id: number };

type CapabilityKey =
  | "computerUse"
  | "browserUse"
  | "webSearch"
  | "mcp"
  | "skills"
  | "toolSearch"
  | "codeMode";

type AppearanceSettings = {
  theme: LoomThemePreference;
  scale: "90" | "100" | "110" | "120" | "130";
  density: "compact" | "comfortable" | "spacious";
  reducedMotion: boolean;
  conversationWidth: "focused" | "balanced" | "wide";
  sidebarWidth: "compact" | "standard" | "wide";
  inspectorWidth: "compact" | "standard" | "wide";
  chatFontSize: number;
  messageLineHeight: "compact" | "comfortable" | "relaxed";
  ambientEffects: boolean;
  petEnabled: boolean;
  codeFont: string;
  codeFontSize: number;
  codeLineHeight: "compact" | "comfortable" | "relaxed";
  codeWrap: boolean;
};

type TerminalSettings = {
  shell: "powershell" | "cmd" | "git-bash" | "wsl";
  encoding: "utf-8" | "system";
  commandTimeoutSeconds: number;
  preserveBackgroundProcesses: boolean;
};

type BrowserConnectionMode = "auto" | "local-launch" | "cdp-attach" | "extension" | "embedded";

type BrowserSettings = {
  mode: BrowserConnectionMode;
  cdpUrl: string;
  preferredEngine: "edge" | "chrome" | "system";
  persistSessions: boolean;
  modelSelectsConnection: boolean;
  allowPrivateNetworks: boolean;
};

type ComputerSettings = {
  verifyActions: boolean;
  screenshotQuality: "fast" | "balanced" | "high";
};

type PrivacySettings = {
  telemetry: boolean;
  crashReports: boolean;
};

type DesktopSettings = LoomSettings & {
  appearance?: Partial<AppearanceSettings>;
  shortcuts?: Partial<ShortcutSettings>;
  terminal?: Partial<TerminalSettings>;
  browser?: Partial<BrowserSettings>;
  computer?: Partial<ComputerSettings>;
  privacy?: Partial<PrivacySettings>;
};

type RuntimeView = InitializeResult["runtime"] & {
  settings?: DesktopSettings;
  capabilityStatus?: Partial<Record<CapabilityKey, Record<string, unknown>>>;
  registeredToolCount?: number;
  exposedToolCount?: number;
};

type BrowserExtensionSetupResult = Awaited<ReturnType<Window["loom"]["setupBrowserExtension"]>> & {
  browser: "edge" | "chrome";
};

interface SettingsPageProps {
  runtime: RuntimeView;
  models: ModelSnapshot | null;
  threadId?: string | null;
  running: boolean;
  onRefreshModels?(forceRefresh?: boolean): Promise<ModelSnapshot> | void;
  onClose(): void;
  onOpenThread?(id: string): Promise<void>;
}

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

const LOCAL_DESKTOP_SETTINGS_KEY = DESKTOP_SETTINGS_STORAGE_KEY;
const BROWSER_AUTO_MIGRATION_KEY = "loom.settings.browser-auto-default.v1";
const SETTINGS_UPDATE_PREFIX = "__setting__:";


const DEFAULT_CAPABILITIES: Record<CapabilityKey, boolean> = {
  computerUse: true,
  browserUse: true,
  webSearch: true,
  mcp: true,
  skills: true,
  toolSearch: true,
  codeMode: true,
};

const DEFAULT_APPEARANCE: AppearanceSettings = {
  theme: "dark",
  scale: "100",
  density: "comfortable",
  reducedMotion: false,
  conversationWidth: "balanced",
  sidebarWidth: "standard",
  inspectorWidth: "standard",
  chatFontSize: 14,
  messageLineHeight: "comfortable",
  ambientEffects: true,
  petEnabled: DEFAULT_PET_ENABLED,
  codeFont: "system",
  codeFontSize: 12,
  codeLineHeight: "comfortable",
  codeWrap: false,
};

const CONVERSATION_WIDTH_VALUES: Record<AppearanceSettings["conversationWidth"], string> = {
  focused: "740px",
  balanced: "860px",
  wide: "1040px",
};

const SIDEBAR_WIDTH_VALUES: Record<AppearanceSettings["sidebarWidth"], string> = {
  compact: "220px",
  standard: "252px",
  wide: "292px",
};

const INSPECTOR_WIDTH_VALUES: Record<AppearanceSettings["inspectorWidth"], string> = {
  compact: "280px",
  standard: "316px",
  wide: "360px",
};

const MESSAGE_LINE_HEIGHT_VALUES: Record<AppearanceSettings["messageLineHeight"], string> = {
  compact: "1.54",
  comfortable: "1.72",
  relaxed: "1.90",
};

const CODE_LINE_HEIGHT_VALUES: Record<AppearanceSettings["codeLineHeight"], string> = {
  compact: "1.46",
  comfortable: "1.62",
  relaxed: "1.80",
};

const DEFAULT_TERMINAL: TerminalSettings = {
  shell: "powershell",
  encoding: "utf-8",
  commandTimeoutSeconds: 120,
  preserveBackgroundProcesses: true,
};

const DEFAULT_BROWSER: BrowserSettings = {
  mode: "auto",
  cdpUrl: "",
  preferredEngine: "edge",
  persistSessions: true,
  modelSelectsConnection: false,
  allowPrivateNetworks: false,
};

const BROWSER_MODE_OPTIONS: { value: BrowserConnectionMode; label: string; detail: string }[] = [
  {
    value: "auto",
    label: "Current browser · recommended",
    detail: "Use the current tab. Stop if the bridge is offline; do not switch browsers.",
  },
  {
    value: "extension",
    label: "Current browser only",
    detail: "Require the Loom extension and drive the tab you are looking at. Never fall back to another browser.",
  },
  {
    value: "embedded",
    label: "Loom built-in browser",
    detail: "Control the same embedded tabs after enabling AI for a tab in Loom Desktop.",
  },
  {
    value: "local-launch",
    label: "Isolated browser",
    detail: "Always launch a separate visible browser owned by Loom. Your everyday browser stays untouched.",
  },
  {
    value: "cdp-attach",
    label: "Developer CDP attach",
    detail: "Drive a Chrome/Edge already running with an explicit loopback remote-debugging port.",
  },
];

const DEFAULT_COMPUTER: ComputerSettings = {
  verifyActions: true,
  screenshotQuality: "balanced",
};

const DEFAULT_PRIVACY: PrivacySettings = {
  telemetry: false,
  crashReports: false,
};

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Loom",
    items: [
      { key: "general", label: "General", icon: Settings2 },
      { key: "ambient", label: "Ambient assistant", icon: Activity },
      { key: "projectRuns", label: "Persistent projects", icon: FolderOpen },
      { key: "appearance", label: "Appearance", icon: Palette },
      { key: "models", label: "Models", icon: Cpu },
      { key: "capabilities", label: "Capabilities", icon: Blocks },
      { key: "memory", label: "Memory", icon: BrainCircuit },
      { key: "permissions", label: "Permissions", icon: ShieldCheck },
    ],
  },
  {
    label: "Integrations",
    items: [
      { key: "computer", label: "Computer Use", icon: Monitor },
      { key: "browser", label: "Browser", icon: Globe2 },
      { key: "websearch", label: "Web Search", icon: Search },
      { key: "terminal", label: "Terminal", icon: Terminal },
      { key: "plugins", label: "Plugins", icon: Plug },
      { key: "connectors", label: "Connectors", icon: Link2 },
      { key: "mcp", label: "MCP", icon: Blocks },
      { key: "skills", label: "Skills", icon: Sparkles },
    ],
  },
  {
    label: "Advanced",
    items: [
      { key: "shortcuts", label: "Keyboard shortcuts", icon: Keyboard },
      { key: "privacy", label: "Privacy & data", icon: Database },
      { key: "developer", label: "Developer", icon: Wrench },
    ],
  },
];

// The order the sidebar shows the pages in. Navigation direction comes from here,
// so the page travels the way the highlight does.
const NAV_ORDER: PageKey[] = NAV_GROUPS.flatMap((group) => group.items.map((item) => item.key));

const CAPABILITIES: {
  key: CapabilityKey;
  title: string;
  description: string;
  icon: LucideIcon;
  detailPage?: PageKey;
}[] = [
  { key: "computerUse", title: "Computer Use", description: "Use screenshots, accessibility tools, mouse, and keyboard.", icon: Monitor, detailPage: "computer" },
  { key: "browserUse", title: "Browser Use", description: "Use the current browser, an isolated session, or a local connection.", icon: Globe2, detailPage: "browser" },
  { key: "webSearch", title: "Web Search", description: "Search public sources with your configured provider.", icon: Search },
  { key: "mcp", title: "MCP tools", description: "Use tools from connected MCP servers.", icon: Plug, detailPage: "mcp" },
  { key: "skills", title: "Skills", description: "Load reusable workflows when needed.", icon: Sparkles, detailPage: "skills" },
  { key: "toolSearch", title: "Tool Search", description: "Find integration tools when needed.", icon: Wrench },
  { key: "codeMode", title: "Code Mode", description: "Compose multiple tool calls within execution limits.", icon: Code2 },
];

function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function text(value: unknown, fallback = "—"): string {
  const resolved = String(value ?? "").trim();
  return resolved || fallback;
}

function bool(value: unknown): boolean {
  return value === true;
}

function runtimeAvailable(status?: Record<string, unknown>): boolean | null {
  if (!status) return null;
  if (typeof status.enabled === "boolean") return status.enabled;
  if (typeof status.available === "boolean") return status.available;
  if (status.error) return false;
  return true;
}

function capabilityLabel(status: Record<string, unknown> | undefined, userEnabled: boolean): { text: string; tone: string } {
  if (!userEnabled) return { text: "Off", tone: "off" };
  const available = runtimeAvailable(status);
  if (available === null) return { text: "On", tone: "ready" };
  if (!available) return { text: "Runtime missing", tone: "warning" };
  return { text: "Ready", tone: "ready" };
}

function capabilityStatusText(status: Record<string, unknown> | undefined): string {
  const authorization = status?.accountAuthorization as Record<string, unknown> | undefined;
  if (authorization) {
    const state = String(authorization.status || "not_checked");
    if (state === "confirmed" && authorization.allowed === false) return "Account automation permission disabled";
    const labels: Record<string, string> = {
      credential_missing: "Sign in to verify automation access",
      authentication_failed: "Account session expired; sign in again",
      service_unavailable: "Authorization service unavailable; not a permission denial",
      invalid_response: "Invalid authorization service response",
      configuration_invalid: "Account service configuration invalid",
      not_checked: "Checking account automation access",
    };
    if (labels[state]) return labels[state];
  }
  const available = runtimeAvailable(status);
  if (available === null) return "Not reported";
  if (!available) return "Backend missing";
  return "Ready";
}

function readLocalSettings(): Partial<DesktopSettings> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LOCAL_DESKTOP_SETTINGS_KEY) || "{}");
    if (!parsed || typeof parsed !== "object") return {};

    // v2 renderer state eagerly materialized local-launch even when the user had
    // never chosen a browser route. That old default would otherwise override
    // the new server-side auto policy forever. Migrate that ambiguous default
    // once, without touching any other local desktop preference. An explicit
    // isolated-browser choice made after this release is then preserved normally.
    if (!window.localStorage.getItem(BROWSER_AUTO_MIGRATION_KEY)) {
      const browser = parsed.browser && typeof parsed.browser === "object" ? { ...parsed.browser } : undefined;
      if (browser?.mode === "local-launch") {
        delete browser.mode;
        parsed.browser = browser;
      }
      window.localStorage.setItem(BROWSER_AUTO_MIGRATION_KEY, "1");
      window.localStorage.setItem(LOCAL_DESKTOP_SETTINGS_KEY, JSON.stringify(parsed));
    }
    return parsed;
  } catch {
    return {};
  }
}

function mergedSettings(runtime: RuntimeView): DesktopSettings {
  const local = readLocalSettings();
  const server = runtime.settings ?? ({ schemaVersion: 2, capabilities: {} } as DesktopSettings);
  return {
    ...server,
    ...local,
    schemaVersion: Number(server.schemaVersion || local.schemaVersion || 2),
    agent: server.agent,
    capabilities: {
      ...DEFAULT_CAPABILITIES,
      ...(server.capabilities ?? {}),
      ...(local.capabilities ?? {}),
    },
    appearance: { ...DEFAULT_APPEARANCE, ...(server.appearance ?? {}), ...(local.appearance ?? {}) },
    shortcuts: mergeShortcutSettings({ ...(server.shortcuts ?? {}), ...(local.shortcuts ?? {}) }),
    terminal: { ...DEFAULT_TERMINAL, ...(server.terminal ?? {}), ...(local.terminal ?? {}) },
    browser: { ...DEFAULT_BROWSER, ...(server.browser ?? {}), ...(local.browser ?? {}) },
    computer: { ...DEFAULT_COMPUTER, ...(server.computer ?? {}), ...(local.computer ?? {}) },
    privacy: { ...DEFAULT_PRIVACY, ...(server.privacy ?? {}), ...(local.privacy ?? {}) },
  };
}

function writeLocalSettings(settings: DesktopSettings): void {
  try {
    const { agent: _hostPreference, ...localSettings } = settings;
    window.localStorage.setItem(LOCAL_DESKTOP_SETTINGS_KEY, JSON.stringify(localSettings));
  } catch {
    // Keep the current in-memory state when renderer storage is unavailable.
  }
}

function setNestedSetting(settings: DesktopSettings, path: string, value: unknown): DesktopSettings {
  const [section, key] = path.split(".", 2);
  return {
    ...settings,
    [section]: {
      ...((settings as unknown as Record<string, unknown>)[section] as Record<string, unknown> | undefined),
      [key]: value,
    },
  } as DesktopSettings;
}

function applyAppearance(settings: DesktopSettings): void {
  const appearance = { ...DEFAULT_APPEARANCE, ...(settings.appearance ?? {}) } as AppearanceSettings;
  applyThemePreference(appearance.theme);
  document.documentElement.style.setProperty("zoom", String(Number(appearance.scale) / 100));
  document.documentElement.dataset.loomReducedMotion = String(appearance.reducedMotion);
  document.documentElement.dataset.loomDensity = appearance.density;
  document.documentElement.dataset.loomAmbientEffects = String(appearance.ambientEffects);
  applyPetVisibility(appearance.petEnabled);
  document.documentElement.dataset.loomCodeWrap = String(appearance.codeWrap);
  document.documentElement.style.setProperty("--content-width", CONVERSATION_WIDTH_VALUES[appearance.conversationWidth] ?? CONVERSATION_WIDTH_VALUES.balanced);
  document.documentElement.style.setProperty("--sidebar-width", SIDEBAR_WIDTH_VALUES[appearance.sidebarWidth] ?? SIDEBAR_WIDTH_VALUES.standard);
  document.documentElement.style.setProperty("--inspector-width", INSPECTOR_WIDTH_VALUES[appearance.inspectorWidth] ?? INSPECTOR_WIDTH_VALUES.standard);
  document.documentElement.style.setProperty("--loom-chat-font-size", `${appearance.chatFontSize}px`);
  document.documentElement.style.setProperty("--loom-message-line-height", MESSAGE_LINE_HEIGHT_VALUES[appearance.messageLineHeight] ?? MESSAGE_LINE_HEIGHT_VALUES.comfortable);
  const mono = appearance.codeFont === "system"
    ? "'Loom Mono', 'Cascadia Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"
    : `${appearance.codeFont}, "Loom Mono", ui-monospace, monospace`;
  document.documentElement.style.setProperty("--loom-code-font", mono);
  document.documentElement.style.setProperty("--loom-code-font-size", `${appearance.codeFontSize}px`);
  document.documentElement.style.setProperty("--loom-code-line-height", CODE_LINE_HEIGHT_VALUES[appearance.codeLineHeight] ?? CODE_LINE_HEIGHT_VALUES.comfortable);
}

function SettingSwitch({ checked, disabled, label, onChange }: { checked: boolean; disabled?: boolean; label: string; onChange(value: boolean): void }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`settings-switch ${checked ? "on" : ""}`} disabled={disabled} onClick={() => onChange(!checked)}>
      <span />
    </button>
  );
}

function StatusPill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`settings-status-pill ${tone}`}><span className="settings-status-dot" />{children}</span>;
}

function Section({ title, caption, className, children, action }: { title: string; caption?: string; className?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className={className ? `settings-section ${className}` : "settings-section"}>
      <div className="settings-section-heading"><h2>{title}</h2>{caption ? <p>{caption}</p> : null}{action}</div>
      {children}
    </section>
  );
}

function DetailRow({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="settings-detail-row">
      <div><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</div>
      <code title={value}>{value}</code>
    </div>
  );
}

function PreferenceRow({ icon: Icon, title, detail, children }: { icon: LucideIcon; title: string; detail: string; children: ReactNode }) {
  return (
    <div className="mature-preference-row">
      <span className="mature-preference-icon"><Icon size={17} strokeWidth={1.8} /></span>
      <div className="mature-preference-copy"><strong>{title}</strong><span>{detail}</span></div>
      <div className="mature-preference-control">{children}</div>
    </div>
  );
}

function SelectControl({ value, options, onChange, label }: { value: string; options: { value: string; label: string }[]; onChange(value: string): void; label: string }) {
  return (
    <select className="mature-select" value={value} aria-label={label} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
    </select>
  );
}

const SCALE_OPTIONS = (["90", "100", "110", "120", "130"] as const).map((value) => ({ value, label: `${value}%` }));

// A summary value that plays a one-off "settled" cue when it changes. Nothing
// animates on first mount (data-changed stays false), so opening the page adds
// no extra motion on top of the page transition.
function LiveValue({ value }: { value: string }) {
  const previous = useRef(value);
  const changes = useRef(0);
  if (previous.current !== value) {
    previous.current = value;
    changes.current += 1;
  }
  return <b key={changes.current} className="live-value" data-changed={changes.current > 0}>{value}</b>;
}

// One thumb glides between equal-width cells (transform only), so choosing a
// value reads as a single continuous motion instead of two unrelated buttons.
function SegmentedControl<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange(value: T): void; label: string }) {
  const index = Math.max(0, options.findIndex((option) => option.value === value));
  return (
    <div className="appearance-segmented" role="group" aria-label={label} style={{ "--seg-count": options.length, "--seg-index": index } as CSSProperties}>
      <span className="appearance-segmented-thumb" aria-hidden="true" />
      {options.map((option) => (
        <button type="button" key={option.value} className={option.value === value ? "active" : ""} aria-pressed={option.value === value} onClick={() => onChange(option.value)}>{option.label}</button>
      ))}
    </div>
  );
}

export function SettingsPage({ runtime, models, threadId, running, onRefreshModels, onClose, onOpenThread }: SettingsPageProps) {
  const activeRoute = useSettingsRoute();
  const { language } = useI18n();
  const [page, setPage] = useState<PageKey>(() => activeRoute as PageKey);
  const settingsScrollRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  // Set by navigateToPage, consumed by the layout effect that plays the page in.
  const pageFlowRef = useRef<{ flow: PageFlow; travel: number } | null>(null);
  const [query, setQuery] = useState("");
  const [settings, setSettings] = useState<DesktopSettings>(() => mergedSettings(runtime));
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [modelState, setModelState] = useState<ModelSnapshot | null>(models);
  const [busyCapability, setBusyCapability] = useState<CapabilityKey | null>(null);
  const [notice, setNoticeState] = useState<SettingsNotice | null>(null);
  const noticeSeqRef = useRef(0);
  // Each notice gets its own id so the toast countdown restarts even when two
  // messages arrive back to back.
  const setNotice = (next: Omit<SettingsNotice, "id"> | null) => {
    setNoticeState(next ? { ...next, id: ++noticeSeqRef.current } : null);
  };
  const noticePresence = useMotionPresence(Boolean(notice), RETAIN.fast);
  const lastNoticeRef = useRef<SettingsNotice | null>(notice);
  if (notice) lastNoticeRef.current = notice;
  const visibleNotice = notice ?? lastNoticeRef.current;
  const [browserSetupBusy, setBrowserSetupBusy] = useState(false);
  const [browserSetup, setBrowserSetup] = useState<BrowserExtensionSetupResult | null>(null);
  const [browserConnectionCheck, setBrowserConnectionCheck] = useState<"idle" | "checking" | "connected" | "offline">("idle");
  // Held locally so the endpoint can be typed without a round trip per keystroke.
  const [cdpDraft, setCdpDraft] = useState(settings.browser?.cdpUrl ?? DEFAULT_BROWSER.cdpUrl);

  const navigateToPage = (nextPage: PageKey) => {
    if (nextPage === page) {
      setSettingsRoute(nextPage);
      settingsScrollRef.current?.scrollTo({ top: 0, behavior: "instant" });
      return;
    }

    // While the layout is still clean, note how far the sidebar highlight has to
    // travel. The page travels the same way (down the list = up from below).
    const travel = prepareNavTravel(navRef.current, nextPage);
    const forward = travel !== 0 ? travel > 0 : NAV_ORDER.indexOf(nextPage) > NAV_ORDER.indexOf(page);
    pageFlowRef.current = { flow: forward ? "forward" : "backward", travel };

    // Commit the destination immediately: no exit to wait for, no timers, no
    // second render. The new page is keyed (see the surface below), so it mounts
    // fresh and settings-page-motion.css lifts it into place on the first frame.
    setPage(nextPage);
    setSettingsRoute(nextPage);
    settingsScrollRef.current?.scrollTo({ top: 0, behavior: "instant" });
  };

  // Play the navigation: the sidebar highlight travels and the new page rises in.
  // The work itself waits for the next frame (settingsMotion.ts), so this effect
  // costs the click nothing.
  useLayoutEffect(() => {
    const surface = surfaceRef.current;
    const pending = pageFlowRef.current;
    pageFlowRef.current = null;
    if (!surface || !pending || prefersReducedMotionNow()) return;
    return playPageTransition(surface, navRef.current, pending.flow, pending.travel);
  }, [page]);

  useEffect(() => {
    const merged = mergedSettings(runtime);
    setSettings((current) => ({ ...merged, ...current, agent: merged.agent ?? current.agent, capabilities: { ...merged.capabilities, ...current.capabilities } }));
  }, [runtime.settings]);

  useEffect(() => {
    setModelState(models);
  }, [models]);

  useEffect(() => {
    if (page !== "models" || !onRefreshModels) return;
    let disposed = false;
    void Promise.resolve(onRefreshModels(true))
      .then((snapshot) => {
        if (!disposed && snapshot) setModelState(snapshot);
      })
      .catch(() => {
        // Keep the existing settings snapshot on transient provider failures.
        // The desktop model manager owns last-known-good fallback behavior.
      });
    return () => { disposed = true; };
  }, [onRefreshModels, page]);

  useEffect(() => {
    applyAppearance(settings);
  }, [settings.appearance]);

  useEffect(() => {
    let disposed = false;
    void window.loom.call<{ settings?: DesktopSettings }>("settings/get", {})
      .then((result) => {
        if (disposed || !result.settings) return;
        const local = readLocalSettings();
        const next = {
          ...result.settings,
          ...local,
          agent: result.settings.agent,
          capabilities: { ...DEFAULT_CAPABILITIES, ...(result.settings.capabilities ?? {}), ...(local.capabilities ?? {}) },
          appearance: { ...DEFAULT_APPEARANCE, ...(result.settings.appearance ?? {}), ...(local.appearance ?? {}) },
          shortcuts: mergeShortcutSettings({ ...(result.settings.shortcuts ?? {}), ...(local.shortcuts ?? {}) }),
          terminal: { ...DEFAULT_TERMINAL, ...(result.settings.terminal ?? {}), ...(local.terminal ?? {}) },
          browser: { ...DEFAULT_BROWSER, ...(result.settings.browser ?? {}), ...(local.browser ?? {}) },
          computer: { ...DEFAULT_COMPUTER, ...(result.settings.computer ?? {}), ...(local.computer ?? {}) },
          privacy: { ...DEFAULT_PRIVACY, ...(result.settings.privacy ?? {}), ...(local.privacy ?? {}) },
        } as DesktopSettings;
        setSettings(next);
        applyProgressFeedback(next.agent?.progressFeedback);
        setCdpDraft(next.browser?.cdpUrl ?? DEFAULT_BROWSER.cdpUrl);
        applyAppearance(next);
      })
      .catch(() => undefined);
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const filteredGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return NAV_GROUPS;
    // Match the label the user sees, not only its English source.
    const matches = (label: string) => label.toLowerCase().includes(needle)
      || translateSettingsText(label, language).toLowerCase().includes(needle);
    return NAV_GROUPS
      .map((group) => ({ ...group, items: group.items.filter((item) => matches(item.label)) }))
      .filter((group) => group.items.length);
  }, [query, language]);

  const capabilityEnabled = (key: CapabilityKey) => settings.capabilities?.[key] !== false;
  const statusFor = (key: CapabilityKey) => runtime.capabilityStatus?.[key];
  const computerStatus = statusFor("computerUse");
  const browserStatus = statusFor("browserUse");
  const skillsStatus = statusFor("skills");
  const mcpStatus = statusFor("mcp");
  const webStatus = statusFor("webSearch");
  const currentModel = modelState?.current ?? models?.current;

  const setupBrowserExtension = async (browser: "edge" | "chrome") => {
    setBrowserSetupBusy(true);
    try {
      const bridge = (browserStatus?.extension_bridge as Record<string, unknown> | undefined) ?? {};
      const result = await window.loom.setupBrowserExtension(browser, bridge.connected === true);
      setBrowserSetup({ ...result, browser });
      setBrowserConnectionCheck("checking");
      setNotice({
        tone: "success",
        text: result.automaticUpdateRequested
          ? "Extension update installed. Waiting for the browser bridge to reload."
          : `Extension prepared. Its path is already copied: ${result.extensionPath}`,
      });
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBrowserSetupBusy(false);
    }
  };

  const checkBrowserExtensionConnection = async () => {
    setBrowserConnectionCheck("checking");
    try {
      const snapshot = await window.loom.call<{ capabilityStatus?: Record<string, Record<string, unknown>> }>("runtime/status", {});
      const browser = snapshot.capabilityStatus?.browserUse ?? {};
      const extension = (browser.extension_bridge as Record<string, unknown> | undefined) ?? {};
      const reportedVersion = String(extension.last_client_version || "");
      const connected = extension.connected === true
        && (!browserSetup?.desiredVersion || reportedVersion === browserSetup.desiredVersion);
      setBrowserConnectionCheck(connected ? "connected" : "offline");
      return connected;
    } catch {
      setBrowserConnectionCheck("offline");
      return false;
    }
  };

  useEffect(() => {
    if (!browserSetup || browserConnectionCheck === "connected") return;
    let disposed = false;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      void checkBrowserExtensionConnection().then((connected) => {
        if (disposed || !connected) return;
        window.clearInterval(timer);
      });
      if (attempts >= 30) window.clearInterval(timer);
    }, 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [browserSetup, browserConnectionCheck === "connected"]);

  const saveProgressFeedback = async (mode: ProgressFeedbackMode) => {
    if (feedbackSaving || settings.agent?.progressFeedback === mode) return;
    setFeedbackSaving(true);
    try {
      const result = await window.loom.call<{ settings?: DesktopSettings }>("settings/set", {
        path: "agent.progressFeedback", value: mode,
      });
      if (result.settings?.agent?.progressFeedback !== mode) throw new Error("Host did not confirm this preference");
      setSettings(current => ({ ...current, agent: result.settings!.agent }));
      applyProgressFeedback(mode);
      setNotice({ tone: "success", text: language === "zh-CN" ? "进度反馈已保存，从下一次模型请求生效。" : "Progress feedback saved for the next model request." });
    } catch (error) {
      setNotice({ tone: "error", text: (language === "zh-CN" ? "进度反馈未保存：" : "Progress feedback was not saved: ") + String((error as Error)?.message || error) });
    } finally {
      setFeedbackSaving(false);
    }
  };

  const saveSetting = async (path: string, value: unknown, successText?: string) => {
    const next = setNestedSetting(settings, path, value);
    setSettings(next);
    writeLocalSettings(next);
    try {
      const envelope = `${SETTINGS_UPDATE_PREFIX}${JSON.stringify({ path, value })}`;
      await window.loom.call("settings/set", { capability: envelope, enabled: true });
      setNotice({ tone: "success", text: successText || "Preference saved." });
    } catch {
      setNotice({ tone: "success", text: `${successText || "Preference saved."} Local desktop preference is active.` });
    }
  };

  // Appearance and shortcuts are local-first, so saveSetting treats a server
  // failure as harmless. The browser connection is the opposite: the runtime is
  // the thing being changed, and it rejects and rolls back anything it cannot
  // honour. Reporting that as "saved" would leave the page showing a browser
  // Loom is not driving.
  const saveBrowserSetting = async (path: string, value: unknown, successText: string) => {
    const previous = settings;
    const next = setNestedSetting(settings, path, value);
    setSettings(next);
    writeLocalSettings(next);
    try {
      const envelope = `${SETTINGS_UPDATE_PREFIX}${JSON.stringify({ path, value })}`;
      const result = (await window.loom.call("settings/set", { capability: envelope, enabled: true })) as
        | { settings?: DesktopSettings; browserWarning?: string }
        | undefined;
      if (result?.browserWarning) {
        setSettings(previous);
        writeLocalSettings(previous);
        setNotice({ tone: "error", text: `Browser connection unchanged: ${result.browserWarning}` });
        return false;
      }
      setNotice({ tone: "success", text: successText });
      return true;
    } catch (error) {
      setSettings(previous);
      writeLocalSettings(previous);
      setNotice({ tone: "error", text: `Could not change the browser connection: ${String((error as Error)?.message || error)}` });
      return false;
    }
  };

  const saveBrowserMode = async (mode: BrowserConnectionMode) => {
    if (mode === "cdp-attach" && !cdpDraft.trim()) {
      setNotice({ tone: "error", text: "Enter the loopback debugging endpoint before attaching to a local browser." });
      return;
    }
    await saveBrowserSetting("browser.mode", mode, "Browser connection updated.");
  };

  const resetAppearance = async () => {
    const next: DesktopSettings = { ...settings, appearance: { ...DEFAULT_APPEARANCE } };
    setSettings(next);
    writeLocalSettings(next);
    applyAppearance(next);
    const writes = Object.entries(DEFAULT_APPEARANCE).map(([key, value]) => {
      const path = `appearance.${key}`;
      const envelope = `${SETTINGS_UPDATE_PREFIX}${JSON.stringify({ path, value })}`;
      return window.loom.call("settings/set", { capability: envelope, enabled: true });
    });
    const results = await Promise.allSettled(writes);
    setNotice({
      tone: "success",
      text: results.every((result) => result.status === "fulfilled")
        ? "Appearance restored to defaults."
        : "Appearance restored locally; server persistence will catch up later.",
    });
  };

  const saveShortcut = async (id: ShortcutCommandId, value: string) => {
    await saveSetting(`shortcuts.${id}`, value, "Shortcut updated.");
    window.dispatchEvent(new Event(SHORTCUTS_CHANGED_EVENT));
  };

  const resetShortcut = async (id: ShortcutCommandId) => {
    await saveShortcut(id, DEFAULT_SHORTCUTS[id]);
  };

  const resetShortcuts = async () => {
    const next: DesktopSettings = { ...settings, shortcuts: { ...DEFAULT_SHORTCUTS } };
    setSettings(next);
    writeLocalSettings(next);
    const writes = (Object.entries(DEFAULT_SHORTCUTS) as [ShortcutCommandId, string][]).map(([id, value]) => {
      const envelope = `${SETTINGS_UPDATE_PREFIX}${JSON.stringify({ path: `shortcuts.${id}`, value })}`;
      return window.loom.call("settings/set", { capability: envelope, enabled: true });
    });
    const results = await Promise.allSettled(writes);
    window.dispatchEvent(new Event(SHORTCUTS_CHANGED_EVENT));
    setNotice({
      tone: "success",
      text: results.every((result) => result.status === "fulfilled")
        ? "Keyboard shortcuts restored to defaults."
        : "Keyboard shortcuts restored locally; server persistence will catch up later.",
    });
  };

  const setCapability = async (key: CapabilityKey, enabled: boolean) => {
    if (running || busyCapability) return;
    const previous = settings;
    const next: DesktopSettings = { ...settings, capabilities: { ...settings.capabilities, [key]: enabled } };
    setSettings(next);
    writeLocalSettings(next);
    setBusyCapability(key);
    try {
      await window.loom.call("settings/set", { capability: key, enabled });
      setNotice({ tone: "success", text: `${CAPABILITIES.find((item) => item.key === key)?.title || key} ${enabled ? "enabled" : "disabled"}.` });
    } catch (cause) {
      setSettings(previous);
      writeLocalSettings(previous);
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBusyCapability(null);
    }
  };

  const copyText = async (value: string, message: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice({ tone: "success", text: message });
    } catch {
      setNotice({ tone: "error", text: "Could not copy to the clipboard." });
    }
  };

  const renderGeneral = () => {
    const modelLabel = currentModel?.name || currentModel?.model || text(runtime.model);
    const permissionLabel = titleCase(text(runtime.defaultPermissionMode, "approval"));
    const enabledCapabilities = CAPABILITIES.filter((item) => capabilityEnabled(item.key)).length;
    const zh = language === "zh-CN";
    return (
      <div className="general-home">
        <div className="settings-page-heading general-home-heading">
          <div><h1>{zh ? "通用" : "General"}</h1><p>{zh ? "让 Loom 更贴合你的工作习惯。" : "Make Loom feel like your workspace."}</p></div>
          <div className={`general-heading-status ${running ? "running" : ""}`} role="status"><i /><span>{running ? (zh ? "正在工作" : "Working") : (zh ? "运行就绪" : "Ready")}</span></div>
        </div>
        <div className="general-runtime-strip" aria-label={zh ? "运行概览" : "Runtime overview"}>
          <button type="button" onClick={() => navigateToPage("models")}><span>{zh ? "当前模型" : "Model"}</span><strong title={modelLabel}>{modelLabel}</strong><ChevronRight size={14} /></button>
          <button type="button" onClick={() => navigateToPage("permissions")}><span>{zh ? "权限模式" : "Permission"}</span><strong>{permissionLabel}</strong><ChevronRight size={14} /></button>
          <div><span>{zh ? "可用工具" : "Tools"}</span><strong>{runtime.exposedToolCount ?? "—"}<small> / {runtime.registeredToolCount ?? "—"}</small></strong></div>
          <button type="button" onClick={() => navigateToPage("capabilities")}><span>{zh ? "已启用能力" : "Capabilities"}</span><strong>{enabledCapabilities}<small> / {CAPABILITIES.length}</small></strong><ChevronRight size={14} /></button>
        </div>
        <Section title={zh ? "使用偏好" : "Preferences"} className="general-home-section">
          <div className="general-preferences">
            <div className="general-overview-card general-language-mount" />
            <div className="general-home-row general-feedback-row">
              <div className="general-row-copy"><strong>{zh ? "进度反馈" : "Progress feedback"}</strong><p>{zh ? "选择 Loom 工作时的反馈节奏。" : "How much Loom shares while working."}</p></div>
              <ProgressFeedbackControl compact mode={settings.agent?.progressFeedback} saving={feedbackSaving} onChange={mode => void saveProgressFeedback(mode)} />
            </div>
            <details className="general-expression-details">
              <summary className="general-home-row"><div className="general-row-copy"><strong>{zh ? "聊天表情" : "Chat expression"}</strong><p>{zh ? "调整表情的频率、风格与数量。" : "Tune sticker frequency, variety, and quantity."}</p></div><ChevronRight size={16} /></summary>
              <div className="general-expression-body">
                <StickerPanel preferences={runtime.stickerPreferences} disabled={running} onSave={async (preferences) => {
                  await window.loom.call("sticker/preferences/set", { preferences });
                }} />
              </div>
            </details>
          </div>
        </Section>
        <Section title={zh ? "工作区" : "Workspace"} className="general-home-section">
          <div className="general-home-row general-workspace-row">
            <FolderOpen size={18} />
            <div className="general-row-copy"><strong>{zh ? "默认工作区" : "Default workspace"}</strong><p title={text(runtime.defaultWorkspace)}>{text(runtime.defaultWorkspace)}</p></div>
            <button type="button" className="general-copy-button" onClick={() => void copyText(text(runtime.defaultWorkspace, ""), zh ? "已复制工作区路径。" : "Workspace path copied.")}><Copy size={13} />{zh ? "复制路径" : "Copy path"}</button>
          </div>
        </Section>
        <Section title={zh ? "更多设置" : "Explore settings"} className="general-home-section">
          <div className="general-home-links">
            <button type="button" onClick={() => navigateToPage("appearance")}><Palette size={18} /><div><strong>{zh ? "外观" : "Appearance"}</strong><span>{zh ? "主题、排版与布局" : "Theme, typography, and layout"}</span></div><ChevronRight size={15} /></button>
            <button type="button" onClick={() => navigateToPage("capabilities")}><Blocks size={18} /><div><strong>{zh ? "能力" : "Capabilities"}</strong><span>{zh ? "工具与集成" : "Tools and integrations"}</span></div><ChevronRight size={15} /></button>
            <button type="button" onClick={() => navigateToPage("developer")}><Wrench size={18} /><div><strong>{zh ? "诊断" : "Diagnostics"}</strong><span>{zh ? "运行状态与详情" : "Runtime health and details"}</span></div><ChevronRight size={15} /></button>
          </div>
        </Section>
        {!isLoomWebRuntime() ? <details className="general-system-details" open>
          <summary className="general-home-row"><div className="general-row-copy"><strong>{zh ? "软件更新" : "Software update"}</strong><p>{zh ? "版本信息与自动更新偏好。" : "Version information and update preferences."}</p></div><ChevronRight size={16} /></summary>
          <div className="general-update-mount" />
        </details> : null}
      </div>
    );
  };

  const renderAppearance = () => {
    const appearance = { ...DEFAULT_APPEARANCE, ...(settings.appearance ?? {}) } as AppearanceSettings;
    const densityLabel = titleCase(appearance.density);
    const widthLabel = titleCase(appearance.conversationWidth);
    const px = (value: string) => Number.parseInt(value, 10);
    // The mock workspace is driven entirely by these custom properties, so a
    // change in any layout preference is visible (and animates) before the user
    // leaves this page.
    const previewStyle = {
      "--pv-sidebar": px(SIDEBAR_WIDTH_VALUES[appearance.sidebarWidth] ?? SIDEBAR_WIDTH_VALUES.standard),
      "--pv-inspector": px(INSPECTOR_WIDTH_VALUES[appearance.inspectorWidth] ?? INSPECTOR_WIDTH_VALUES.standard),
      "--pv-conversation": px(CONVERSATION_WIDTH_VALUES[appearance.conversationWidth] ?? CONVERSATION_WIDTH_VALUES.balanced),
      "--pv-text": appearance.chatFontSize,
      "--pv-leading": MESSAGE_LINE_HEIGHT_VALUES[appearance.messageLineHeight] ?? MESSAGE_LINE_HEIGHT_VALUES.comfortable,
    } as CSSProperties;
    return (
      <>
        <div className="settings-page-heading settings-heading-with-action">
          <div><h1>Appearance</h1><p>Adjust the theme, layout, and reading experience.</p></div>
          <button className="mature-action-button" type="button" onClick={() => void resetAppearance()}><RefreshCw size={14} />Reset appearance</button>
        </div>

        <div className="appearance-overview-card">
          <div className="appearance-overview-copy">
            <span className="settings-eyebrow">Preview</span>
            <strong>Live workspace</strong>
            <p>Layout, message typography, motion, and code preferences update immediately. The preview reflects the current reading profile.</p>
            <div className="appearance-overview-meta">
              <span><em>Theme</em><LiveValue value={titleCase(appearance.theme)} /></span>
              <span><em>Scale</em><LiveValue value={`${appearance.scale}%`} /></span>
              <span><em>Density</em><LiveValue value={densityLabel} /></span>
              <span><em>Width</em><LiveValue value={widthLabel} /></span>
              <span><em>Text</em><LiveValue value={`${appearance.chatFontSize}px`} /></span>
              <span><em>Ambient</em><LiveValue value={appearance.ambientEffects ? "On" : "Off"} /></span>
            </div>
          </div>
          <div className="appearance-workspace-preview" aria-hidden="true" data-density={appearance.density} data-ambient={appearance.ambientEffects ? "on" : "off"} style={previewStyle}>
            <div className="appearance-preview-sidebar"><i className="pv-mark" /><i className="pv-row" /><i className="pv-row current" /><i className="pv-row" /></div>
            <div className="appearance-preview-main">
              <div className="appearance-preview-conversation">
                <i className="pv-user" />
                <div className="pv-reply"><i /><i /><i /><i className="accent" /></div>
                <div className="pv-activity"><i className="dot" /><i className="bar" /></div>
                <i className="pv-composer" />
              </div>
            </div>
            <div className="appearance-preview-inspector"><i className="pv-head" /><i className="pv-row" /><i className="pv-row" /></div>
          </div>
        </div>

        <Section title="Theme" caption="Choose a theme, or follow your device.">
          <div className="appearance-theme-picker" role="radiogroup" aria-label="Loom theme">
            {([
              { value: "system", label: "System", detail: "Follow your device", icon: Monitor },
              { value: "light", label: "Light", detail: "Bright neutral workspace", icon: Sun },
              { value: "dark", label: "Dark", detail: "Focused low-light workspace", icon: Moon },
            ] as const).map((option) => {
              const Icon = option.icon;
              const active = appearance.theme === option.value;
              return (
                <button
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={`appearance-theme-option ${active ? "active" : ""}`}
                  key={option.value}
                  onClick={() => void saveSetting("appearance.theme", option.value, `Theme set to ${option.label}.`)}
                >
                  <span className={`appearance-theme-preview ${option.value}`} aria-hidden="true">
                    <i className="appearance-theme-preview-sidebar" />
                    <i className="appearance-theme-preview-content"><b /><b /><b /></i>
                    <span className="appearance-theme-badge"><Check size={12} strokeWidth={2.6} /></span>
                  </span>
                  <span className="appearance-theme-option-copy">
                    <span className="appearance-theme-option-title">
                      <Icon size={15} strokeWidth={1.8} />
                      <strong>{option.label}</strong>
                    </span>
                    <span>{option.detail}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </Section>

        <Section title="Interface" caption="Size, spacing, and motion. Changes apply immediately.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Type} title="Interface scale" detail="Resize the entire interface."><SegmentedControl label="Interface scale" value={appearance.scale} options={SCALE_OPTIONS} onChange={(scale) => void saveSetting("appearance.scale", scale, `Interface scale set to ${scale}%.`)} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Content density" detail="Set the spacing between interface elements."><SelectControl label="Content density" value={appearance.density} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "spacious", label: "Spacious" }]} onChange={(value) => void saveSetting("appearance.density", value)} /></PreferenceRow>
            <PreferenceRow icon={Moon} title="Reduce motion" detail="Minimize decorative transitions, pulses, and status animation."><SettingSwitch checked={appearance.reducedMotion} label="Reduce motion" onChange={(value) => void saveSetting("appearance.reducedMotion", value)} /></PreferenceRow>
            <PreferenceRow icon={Sparkles} title="Ambient effects" detail="Show the subtle conversation glow and background atmosphere behind messages."><SettingSwitch checked={appearance.ambientEffects} label="Ambient conversation effects" onChange={(value) => void saveSetting("appearance.ambientEffects", value)} /></PreferenceRow>
            <PreferenceRow icon={Bot} title="Loom pet" detail="Show the interactive companion above the message box. Hidden by default."><SettingSwitch checked={appearance.petEnabled === true} label="Loom pet" onChange={(value) => void saveSetting("appearance.petEnabled", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="Workspace layout" caption="Set the width of the conversation and side panels.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Monitor} title="Conversation width" detail="Set the maximum width of messages."><SelectControl label="Conversation width" value={appearance.conversationWidth} options={[{ value: "focused", label: "Focused · 740px" }, { value: "balanced", label: "Balanced · 860px" }, { value: "wide", label: "Wide · 1040px" }]} onChange={(value) => void saveSetting("appearance.conversationWidth", value)} /></PreferenceRow>
            <PreferenceRow icon={Monitor} title="Sidebar width" detail="Set the width of the project sidebar."><SelectControl label="Sidebar width" value={appearance.sidebarWidth} options={[{ value: "compact", label: "Compact · 220px" }, { value: "standard", label: "Standard · 252px" }, { value: "wide", label: "Wide · 292px" }]} onChange={(value) => void saveSetting("appearance.sidebarWidth", value)} /></PreferenceRow>
            <PreferenceRow icon={Monitor} title="Inspector width" detail="Set the width of the activity panel."><SelectControl label="Inspector width" value={appearance.inspectorWidth} options={[{ value: "compact", label: "Compact · 280px" }, { value: "standard", label: "Standard · 316px" }, { value: "wide", label: "Wide · 360px" }]} onChange={(value) => void saveSetting("appearance.inspectorWidth", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="Conversation reading" caption="Adjust message size and line spacing.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Type} title="Chat text size" detail="Resize messages independently of the interface."><SelectControl label="Chat text size" value={String(appearance.chatFontSize)} options={[12, 13, 14, 15, 16, 17].map((value) => ({ value: String(value), label: `${value}px` }))} onChange={(value) => void saveSetting("appearance.chatFontSize", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Message line spacing" detail="Set the spacing between lines in messages."><SelectControl label="Message line spacing" value={appearance.messageLineHeight} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "relaxed", label: "Relaxed" }]} onChange={(value) => void saveSetting("appearance.messageLineHeight", value)} /></PreferenceRow>
          </div>
          <div className="appearance-reading-preview">
            <span className="appearance-reading-preview-label">Message preview</span>
            <div className="appearance-reading-preview-message"><strong>Loom</strong> keeps the response column readable while preserving the same Agent behavior. This preview uses your current chat text size and line spacing, so you can tune long-form answers before returning to the conversation.</div>
          </div>
        </Section>

        <Section title="Code appearance" caption="Set the font and spacing for code and terminal output.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Code2} title="Code font" detail="Use the system monospace stack or a font installed on this machine."><input className="mature-input" value={appearance.codeFont} onChange={(event) => setSettings(setNestedSetting(settings, "appearance.codeFont", event.target.value))} onBlur={(event) => void saveSetting("appearance.codeFont", event.target.value.trim() || "system")} placeholder="system or JetBrains Mono" /></PreferenceRow>
            <PreferenceRow icon={Type} title="Code font size" detail="Resize code independently of messages."><SelectControl label="Code font size" value={String(appearance.codeFontSize)} options={[10, 11, 12, 13, 14, 15, 16, 17, 18].map((value) => ({ value: String(value), label: `${value}px` }))} onChange={(value) => void saveSetting("appearance.codeFontSize", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Code line spacing" detail="Set the spacing between lines of code."><SelectControl label="Code line spacing" value={appearance.codeLineHeight} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "relaxed", label: "Relaxed" }]} onChange={(value) => void saveSetting("appearance.codeLineHeight", value)} /></PreferenceRow>
            <PreferenceRow icon={Code2} title="Wrap long code" detail="Wrap long code lines instead of requiring horizontal scrolling in message code blocks."><SettingSwitch checked={appearance.codeWrap} label="Wrap long code lines" onChange={(value) => void saveSetting("appearance.codeWrap", value)} /></PreferenceRow>
          </div>
          <div className="appearance-code-preview-head"><span>Live code preview</span><span>{appearance.codeFont === "system" ? "System monospace" : appearance.codeFont} · {appearance.codeFontSize}px</span></div>
          <div className="mature-code-preview"><span>PS C:\Loom&gt;</span> <strong>git status</strong><br /><span>On branch main · working tree clean</span><br /><span>const layout = &#123; width: "{appearance.conversationWidth}", density: "{appearance.density}" &#125;;</span></div>
        </Section>
      </>
    );
  };

  const renderModels = () => (
    <ModelsSettingsPanel
      initialSnapshot={modelState}
      runtimeModel={runtime.model}
      running={running}
      onSnapshot={setModelState}
    />
  );

  const renderCapabilities = () => (
    <>
      <div className="settings-page-heading"><div><h1>Capabilities</h1><p>Choose the tools Loom can use.</p></div><span className="settings-tool-count">{runtime.exposedToolCount ?? "—"} / {runtime.registeredToolCount ?? "—"} tools available</span></div>
      {running ? <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Finish or stop the active turn first.</strong><span>Tool exposure cannot change mid-execution.</span></div></div> : null}
      <Section title="Agent capabilities" caption="Enable tools and check their availability." className="has-capability-list">
        <div className="settings-card capability-list">
          {CAPABILITIES.map((item) => {
            const Icon = item.icon;
            const enabled = capabilityEnabled(item.key);
            const status = statusFor(item.key);
            const provider = item.key === "webSearch" && enabled ? text(status?.provider, "") : "";
            const badge = capabilityLabel(status, enabled);
            // The provider name is its own text node so the surrounding words stay
            // translatable; a single interpolated string could not be matched.
            return <div className="capability-row" key={item.key}><div className="capability-icon"><Icon size={17} /></div><div className="capability-copy"><div className="capability-title-line"><strong>{item.title}</strong><StatusPill tone={badge.tone}>{badge.text}{provider ? ` · ${titleCase(provider)}` : null}</StatusPill></div><span>{item.description}{provider ? <> Active provider: <b>{titleCase(provider)}</b></> : null}</span></div>{item.detailPage ? <button type="button" className="settings-row-link" onClick={() => navigateToPage(item.detailPage!)}><ChevronRight size={15} /></button> : <span className="settings-row-link-spacer" />}<SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label={`Toggle ${item.title}`} onChange={(value) => void setCapability(item.key, value)} /></div>;
          })}
        </div>
      </Section>
    </>
  );

  const renderComputer = () => {
    const enabled = capabilityEnabled("computerUse");
    const badge = capabilityLabel(computerStatus, enabled);
    const prefs = { ...DEFAULT_COMPUTER, ...(settings.computer ?? {}) } as ComputerSettings;
    return <><div className="settings-page-heading settings-heading-with-switch"><div><h1>Computer Use</h1><p>Control desktop apps with screenshots and accessibility tools.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Computer Use" onChange={(value) => void setCapability("computerUse", value)} /></div></div>
      <Section title="Runtime status" action={<div className="settings-log-export-mount" />}><div className="settings-card settings-detail-list"><DetailRow label="Backend" value={capabilityStatusText(computerStatus)} /><DetailRow label="Operator" value={text(computerStatus?.operator, "Not reported")} /><DetailRow label="Grounder" value={text(computerStatus?.grounder, "Not reported")} /><DetailRow label="Observation" value={text(computerStatus?.observation_mode, "Not reported")} /><DetailRow label="Verification" value={text(computerStatus?.verification, "Not reported")} /></div></Section>
      <Section title="Desktop preferences" caption="Set verification and capture preferences."><div className="settings-card mature-preference-list"><PreferenceRow icon={ShieldCheck} title="Verify actions" detail="Check the result after each action."><SettingSwitch checked={prefs.verifyActions} label="Verify Computer Use actions" onChange={(value) => void saveSetting("computer.verifyActions", value)} /></PreferenceRow><PreferenceRow icon={Monitor} title="Screenshot quality" detail="Balance image detail and capture speed."><SelectControl label="Screenshot quality" value={prefs.screenshotQuality} options={[{ value: "fast", label: "Fast" }, { value: "balanced", label: "Balanced" }, { value: "high", label: "High detail" }]} onChange={(value) => void saveSetting("computer.screenshotQuality", value)} /></PreferenceRow></div></Section>
    </>;
  };

  const renderBrowser = () => {
    const enabled = capabilityEnabled("browserUse");
    const badge = capabilityLabel(browserStatus, enabled);
    const prefs = { ...DEFAULT_BROWSER, ...(settings.browser ?? {}) } as BrowserSettings;
    const requestedConnection = text(browserStatus?.requested_browser_connection, prefs.mode);
    const selectedConnection = text(browserStatus?.selected_browser_backend, browserStatus?.browser_connection ? String(browserStatus.browser_connection) : "Not reported");
    const bridge = (browserStatus?.extension_bridge as Record<string, unknown> | undefined) ?? {};
    const extensionConnected = bool(bridge.connected);
    const currentTab = (bridge.current_tab as Record<string, unknown> | undefined) ?? {};
    return <><div className="settings-page-heading settings-heading-with-switch"><div><h1>Browser</h1><p>Use your current browser or a separate session.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Browser Use" onChange={(value) => void setCapability("browserUse", value)} /></div></div>
      <Section title="Browser runtime" action={<div className="settings-log-export-mount" />}><div className="settings-card settings-detail-list"><DetailRow label="Requested backend" value={requestedConnection} /><DetailRow label="Actual backend" value={selectedConnection} /><DetailRow label="Backend" value={text(browserStatus?.backend, capabilityStatusText(browserStatus))} /><DetailRow label="Current browser bridge" value={extensionConnected ? "Connected" : "Not connected"} /><DetailRow label="Browser" value={text(bridge.browser, extensionConnected ? "Chromium browser" : "Not connected")} /><DetailRow label="Current tab" value={text(currentTab.title, currentTab.url ? String(currentTab.url) : "Not reported")} /><DetailRow label="Active sessions" value={String(browserStatus?.active_sessions ?? "Not reported")} /></div></Section>
      <div className={`settings-callout ${extensionConnected ? "ok" : "warning"}`}>{extensionConnected ? <Check size={16} /> : <CircleAlert size={16} />}<div><strong>{extensionConnected ? "Current browser is connected." : "Current Browser Bridge is offline."}</strong><span>{extensionConnected ? "New sessions use this browser’s active tab and login state." : "Loom will stop current-browser tasks instead of opening an isolated browser or using Computer Use."}</span></div></div>
      <Section title="Browser extension" caption="Install the local extension to connect your current browser.">
        <div className="settings-card mature-preference-list">
          <PreferenceRow icon={Plug} title="Install or update" detail="Updates apply automatically. The first installation needs browser confirmation.">
            <div className="settings-inline-actions"><button className="mature-action-button" type="button" disabled={browserSetupBusy} onClick={() => void setupBrowserExtension("edge")}>{extensionConnected ? "Update Edge bridge" : "Install Edge bridge"}</button><button className="mature-action-button secondary" type="button" disabled={browserSetupBusy} onClick={() => void setupBrowserExtension("chrome")}>{extensionConnected ? "Update Chrome bridge" : "Install Chrome bridge"}</button></div>
          </PreferenceRow>
          {browserSetup ? (
            <div className="browser-extension-setup-guide">
              <div className="browser-extension-setup-progress"><span className="done"><Check size={14} />Extension files updated</span>{browserSetup.manualInstallRequired ? <span className="done"><Check size={14} />Path copied</span> : null}<span className={browserConnectionCheck === "connected" ? "done" : "pending"}>{browserConnectionCheck === "connected" ? <Check size={14} /> : <RefreshCw size={14} />} {browserConnectionCheck === "connected" ? `Version ${browserSetup.desiredVersion} connected` : browserConnectionCheck === "checking" ? "Applying update" : "Browser confirmation needed"}</span></div>
              {browserSetup.manualInstallRequired ? <div className="browser-extension-manual-steps">
                <strong>First installation: two browser-confirmed steps remain</strong>
                <ol><li>On <code>{browserSetup.managementUrl}</code>, turn on <b>Developer mode</b>.</li><li>Choose <b>Load unpacked</b>. In the folder picker address bar, paste the exact path below, press Enter, then choose <b>Select folder</b>.</li></ol>
              </div> : <div className="browser-extension-manual-steps"><strong>Update is automatic</strong><span>The installed bridge detects the new files, reloads itself, and reconnects. For the first update from an older build, use “Open setup” once and click Reload.</span></div>}
              <code className="browser-extension-path">{browserSetup.extensionPath}</code>
              <div className="settings-inline-actions browser-extension-actions"><button className="mature-action-button secondary" type="button" onClick={() => void copyText(browserSetup.extensionPath, "Absolute extension path copied.")}><Copy size={14} />Copy path</button><button className="mature-action-button secondary" type="button" onClick={() => void window.loom.revealPath(browserSetup.extensionPath)}><FolderOpen size={14} />Open folder</button><button className="mature-action-button secondary" type="button" onClick={() => void window.loom.setupBrowserExtension(browserSetup.browser, false)}><RefreshCw size={14} />Open setup</button><button className="mature-action-button" type="button" onClick={() => void checkBrowserExtensionConnection()}><Activity size={14} />Check version</button></div>
              {browserSetup.openError || browserSetup.folderError ? <div className="settings-callout-inline"><CircleAlert size={15} /><span>Loom could not open one of the setup windows automatically. Use “Open folder” and “Reopen setup” above. {browserSetup.openError || browserSetup.folderError}</span></div> : null}
            </div>
          ) : null}
        </div>
      </Section>
      <Section title="Browser connection" caption="Choose a connection. The runtime shows the browser actually in use.">
        <div className="settings-card mature-preference-list">
          <PreferenceRow icon={Globe2} title="Connection" detail={BROWSER_MODE_OPTIONS.find((item) => item.value === prefs.mode)?.detail ?? ""}>
            <SelectControl label="Browser connection" value={prefs.mode} options={BROWSER_MODE_OPTIONS.map((item) => ({ value: item.value, label: item.label }))} onChange={(value) => void saveBrowserMode(value as BrowserConnectionMode)} />
          </PreferenceRow>
          {prefs.mode === "cdp-attach" ? (
            <PreferenceRow icon={Plug} title="Debugging endpoint" detail="Loopback only, with an explicit port. Start the browser with --remote-debugging-port first.">
              <input className="mature-input" value={cdpDraft} placeholder="http://127.0.0.1:9222" onChange={(event) => setCdpDraft(event.target.value)} onBlur={(event) => void saveBrowserSetting("browser.cdpUrl", event.target.value.trim(), "Debugging endpoint updated.")} />
            </PreferenceRow>
          ) : null}
          {prefs.mode === "extension" ? (
            <div className="settings-callout-inline"><CircleAlert size={15} /><span>Strict current-browser mode requires the Loom Current Tab Bridge extension. If it is not connected, Browser Use reports the error and does not open another browser.</span></div>
          ) : null}
          <PreferenceRow icon={ShieldAlert} title="Let the model pick the browser" detail="Allow model-selected external connections. Isolated sessions still need an explicit request.">
            <SettingSwitch checked={prefs.modelSelectsConnection} label="Model-selected browser connections" onChange={(value) => void saveBrowserSetting("browser.modelSelectsConnection", value, value ? "The model can now choose the browser." : "The model is restricted to the connection above.")} />
          </PreferenceRow>
          <PreferenceRow icon={ShieldAlert} title="Reach local addresses" detail="Allow localhost and private network access. Off by default.">
            <SettingSwitch checked={prefs.allowPrivateNetworks} label="Allow private network addresses" onChange={(value) => void saveBrowserSetting("browser.allowPrivateNetworks", value, value ? "The browser can now reach local addresses." : "Local addresses are blocked again.")} />
          </PreferenceRow>
          {prefs.modelSelectsConnection ? (
            <div className="settings-callout-inline"><ShieldAlert size={15} /><span>A browser you are signed into exposes those sessions to the model, and any page it opens can try to redirect it. Remote endpoints stay blocked: attaching is limited to 127.0.0.1 and ::1.</span></div>
          ) : null}
        </div>
      </Section>
      <Section title="Browser preferences"><div className="settings-card mature-preference-list"><PreferenceRow icon={Globe2} title="Preferred isolated browser" detail={prefs.mode === "local-launch" ? "Browser used for isolated sessions." : "Used only for explicitly requested isolated sessions."}><SelectControl label="Preferred browser" value={prefs.preferredEngine} options={[{ value: "edge", label: "Microsoft Edge" }, { value: "chrome", label: "Google Chrome" }, { value: "system", label: "System default" }]} onChange={(value) => void saveBrowserSetting("browser.preferredEngine", value, "Preferred isolated browser updated.")} /></PreferenceRow><PreferenceRow icon={Database} title="Persist isolated sessions" detail="Keep cookies and site data between isolated sessions. Current browser data is managed separately."><SettingSwitch checked={prefs.persistSessions} label="Persist isolated browser sessions" onChange={(value) => void saveBrowserSetting("browser.persistSessions", value, value ? "Isolated browser sessions will persist." : "Isolated browser sessions will be ephemeral.")} /></PreferenceRow></div></Section>
    </>;
  };

  const renderWebSearch = () => (
    <SettingsWebSearchPanel
      running={running}
      onNotice={(tone, text) => setNotice({ tone, text })}
    />
  );

  const renderTerminal = () => {
    const prefs = { ...DEFAULT_TERMINAL, ...(settings.terminal ?? {}) } as TerminalSettings;
    const shellLabel = {
      powershell: "PowerShell",
      cmd: "Command Prompt",
      "git-bash": "Git Bash",
      wsl: "WSL",
    }[prefs.shell] ?? "PowerShell";
    const permissionLabel = titleCase(text(runtime.defaultPermissionMode, "approval"));
    const commandTemplates = [
      { name: "Project status", command: "git status --short", detail: "Check workspace changes" },
      { name: "Type check", command: "npm run typecheck", detail: "Check frontend types" },
      { name: "Production build", command: "npm run build", detail: "Build the desktop interface" },
      { name: "Skill list", command: "loom skill list", detail: "List discovered skills" },
    ];

    return (
      <>
        <div className="settings-page-heading terminal-page-heading">
          <div>
            <h1>Terminal</h1>
            <p>Set command defaults and reuse common commands.</p>
          </div>
          <div className="terminal-heading-actions">
            <StatusPill tone={running ? "warning" : "ready"}>{running ? "执行中" : "Ready"}</StatusPill>
            <button className="mature-action-button" type="button" onClick={() => void copyText(shellLabel, "Shell preference copied.")}><Copy size={14} />复制 Shell</button>
          </div>
        </div>

        <div className="terminal-hero-card">
          <div className="terminal-hero-copy">
            <span className="terminal-hero-icon"><Terminal size={20} /></span>
            <div>
              <span>Terminal profile</span>
              <strong>{shellLabel} · {prefs.encoding === "utf-8" ? "UTF-8" : "System encoding"} · {prefs.commandTimeoutSeconds}s</strong>
              <p>从右侧工具栏打开终端，可创建独立 Shell 会话。收起面板后继续运行，关闭会话时结束对应进程。</p>
            </div>
          </div>
          <div className="terminal-quick-actions">
            <button type="button" onClick={() => void copyText("npm run typecheck", "Typecheck command copied.")}><Code2 size={14} />Type check</button>
            <button type="button" onClick={() => void copyText("npm run build", "Build command copied.")}><Wrench size={14} />构建</button>
            <button type="button" onClick={() => void copyText(text(runtime.defaultWorkspace, ""), "Workspace path copied.")}><FolderOpen size={14} />工作区</button>
          </div>
        </div>

        <Section title="运行概览" caption="把默认 Shell、编码、超时、后台进程和权限边界放在同一屏，方便检查。">
          <div className="terminal-stat-grid">
            <div className="terminal-stat-card"><span>Preferred shell</span><strong>{shellLabel}</strong><small>desktop preference</small></div>
            <div className="terminal-stat-card"><span>Terminal encoding</span><strong>{prefs.encoding === "utf-8" ? "UTF-8" : "System"}</strong><small>stdout / stderr</small></div>
            <div className="terminal-stat-card"><span>Command timeout</span><strong>{prefs.commandTimeoutSeconds}s</strong><small>foreground commands</small></div>
            <div className="terminal-stat-card"><span>Background processes</span><strong>{prefs.preserveBackgroundProcesses ? "保留" : "关闭"}</strong><small>managed processes</small></div>
            <div className="terminal-stat-card"><span>权限模式</span><strong>{permissionLabel}</strong><small>permission-aware</small></div>
          </div>
        </Section>

        <Section title="Command defaults" caption="Default values for integrations that support terminal preferences.">
          <div className="settings-card mature-preference-list terminal-preference-card">
            <PreferenceRow icon={Terminal} title="Preferred shell" detail="Preferred shell for compatible integrations."><SelectControl label="Preferred shell" value={prefs.shell} options={[{ value: "powershell", label: "PowerShell" }, { value: "cmd", label: "Command Prompt" }, { value: "git-bash", label: "Git Bash" }, { value: "wsl", label: "WSL" }]} onChange={(value) => void saveSetting("terminal.shell", value)} /></PreferenceRow>
            <PreferenceRow icon={Code2} title="Terminal encoding" detail="Default encoding for terminal output."><SelectControl label="Terminal encoding" value={prefs.encoding} options={[{ value: "utf-8", label: "UTF-8" }, { value: "system", label: "System default" }]} onChange={(value) => void saveSetting("terminal.encoding", value)} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Command timeout" detail="Maximum wait for foreground commands."><SelectControl label="Command timeout" value={String(prefs.commandTimeoutSeconds)} options={[30, 60, 120, 300, 600, 1800].map((value) => ({ value: String(value), label: `${value}s` }))} onChange={(value) => void saveSetting("terminal.commandTimeoutSeconds", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Activity} title="Background processes" detail="Keep managed background processes when supported."><SettingSwitch checked={prefs.preserveBackgroundProcesses} label="Preserve background processes" onChange={(value) => void saveSetting("terminal.preserveBackgroundProcesses", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="会话行为" caption="这些是当前终端执行面的产品边界：默认工作区、环境变量、输出处理和长任务管理。">
          <div className="settings-card terminal-runtime-grid">
            {[
              { icon: FolderOpen, title: "默认工作区", detail: "新命令默认贴近当前项目路径，减少误操作目录。", value: text(runtime.defaultWorkspace, "workspace") },
              { icon: Database, title: "环境变量", detail: "敏感值不在设置页明文展示，运行时按权限注入。", value: "受控" },
              { icon: BrainCircuit, title: "输出摘要", detail: "长 stdout / stderr 保留原始日志，同时给 Agent 提供压缩摘要。", value: "自动" },
              { icon: Activity, title: "长任务", detail: "在终端和后台任务面板中查看输出、跟踪状态或停止进程。", value: "托管" },
              { icon: RefreshCw, title: "重试策略", detail: "失败命令不自动无限重试，避免重复写入或重复部署。", value: "安全" },
              { icon: Copy, title: "命令复用", detail: "常用命令可一键复制，后续可升级为模板库。", value: "就绪" },
            ].map((item) => {
              const Icon = item.icon;
              return <div className="terminal-runtime-item" key={item.title}><span><Icon size={15} /></span><div><strong>{item.title}</strong><p>{item.detail}</p></div><code title={item.value}>{item.value}</code></div>;
            })}
          </div>
        </Section>

        <Section title="安全边界" caption="终端是高风险执行面，这里明确展示不会被 UI 偏好绕过的保护规则。">
          <div className="settings-card terminal-security-grid">
            {[
              "敏感命令继续走 PermissionEngine",
              "工作区写入受权限模式约束",
              "不会因切换 Shell 自动提升权限",
              "后台进程必须可追踪和可终止",
              "不隐藏 stderr / exit code",
              "环境变量与密钥不在 UI 明文展示",
            ].map((item) => <div className="terminal-security-row" key={item}><ShieldCheck size={15} /><span>{item}</span><StatusPill tone="ready">已保护</StatusPill></div>)}
          </div>
        </Section>

        <Section title="Command templates" caption="Copy a command to run in your terminal.">
          <div className="settings-card terminal-template-table">
            <div className="terminal-template-head"><span>Name</span><span>Command</span><span>Purpose</span><span>Actions</span></div>
            {commandTemplates.map((template) => <div className="terminal-template-row" key={template.command}><div className="terminal-name-cell"><Terminal size={14} /><strong>{template.name}</strong></div><code>{template.command}</code><span>{template.detail}</span><div className="terminal-row-actions"><button type="button" onClick={() => void copyText(template.command, `${template.name} command copied.`)}>Copy</button></div></div>)}
          </div>
        </Section>

        <Section title="Diagnostics" caption="Inspect saved preferences and runtime status.">
          <div className="settings-card settings-detail-list">
            <DetailRow label="Settings path" value="terminal.*" detail="Saved with desktop settings." />
            <DetailRow label="Preferred shell" value={prefs.shell} detail="Identifier of the preferred shell." />
            <DetailRow label="Runtime state" value={running ? "Turn active" : "Ready for changes"} detail="Capability changes wait until the active turn ends." />
            <DetailRow label="Process policy" value="Permission-aware" detail="Command execution follows the active permission policy." />
          </div>
        </Section>

        <div className="terminal-footer-strip">
          <span><Terminal size={14} />Shell：{shellLabel}</span>
          <span><Gauge size={14} />Timeout：{prefs.commandTimeoutSeconds}s</span>
          <span><Check size={14} />配置已自动保存</span>
        </div>
      </>
    );
  };

  const renderPlugins = () => <PluginsSettingsPanel running={running} />;

  const renderMcp = () => {
    const enabled = capabilityEnabled("mcp");
    const badge = capabilityLabel(mcpStatus, enabled);
    return <><div className="settings-page-heading settings-heading-with-switch"><div><h1>MCP</h1><p>Connect tools from MCP servers.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle MCP" onChange={(value) => void setCapability("mcp", value)} /></div></div><McpSettingsPanel running={running} enabled={enabled} /></>;
  };

  const renderSkills = () => {
    const enabled = capabilityEnabled("skills");
    const badge = capabilityLabel(skillsStatus, enabled);
    const errors = Array.isArray(skillsStatus?.errors) ? skillsStatus.errors : [];
    const discoveredSkills = Array.isArray(skillsStatus?.skills)
      ? (skillsStatus.skills as Record<string, unknown>[])
      : [];
    const discoveredCount = typeof skillsStatus?.count === "number"
      ? skillsStatus.count
      : discoveredSkills.length || "Not reported";
    const userSkillCount = discoveredSkills.filter((skill) => String(skill.scope || "") === "user").length;
    const installedCount = userSkillCount || discoveredSkills.length || "—";
    const activeSkillCount = Array.isArray(skillsStatus?.active_skills)
      ? skillsStatus.active_skills.length
      : "—";
    const healthText = errors.length ? `${errors.length} issue(s)` : "Ready";
    const installedRows: { name: string; description: string; source: string; status: string; tone: string }[] = discoveredSkills.length
      ? discoveredSkills.map((skill) => ({
        name: text(skill.name, "unnamed-skill"),
        description: text(skill.short_description || skill.description, "Reusable workflow"),
        source: text(skill.source || skill.scope, "workspace"),
        status: String(skill.scope || "") === "user" ? "Installed" : "Discovered",
        tone: String(skill.scope || "") === "user" ? "ready" : "muted",
      }))
      : [];

    return (
      <>
        <div className="settings-page-heading settings-heading-with-switch skills-page-heading">
          <div>
            <h1>技能</h1>
            <p>Manage reusable workflows and their sources.</p>
          </div>
          <div className="settings-master-switch">
            <StatusPill tone={badge.tone}>{badge.text}</StatusPill>
            <SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Skills" onChange={(value) => void setCapability("skills", value)} />
          </div>
        </div>

        <div className="skills-hero-card">
          <div className="skills-hero-copy">
            <div className="skills-hero-icon"><Sparkles size={20} /></div>
            <div>
              <span>Skills Runtime v2</span>
              <strong>{enabled ? "技能系统已启用" : "技能系统已关闭"}</strong>
              <p>Skill 只提供说明和资源，不会绕过 Loom 的权限、沙箱、MCP、浏览器与审批边界。</p>
            </div>
          </div>
          <div className="skills-quick-actions">
            <button type="button" onClick={() => setNotice({ tone: "success", text: "已请求刷新运行时状态。" })}><RefreshCw size={14} />刷新扫描</button>
            <button type="button" onClick={() => void copyText("loom skill install https://github.com/owner/repository", "GitHub install command copied.")}><Globe2 size={14} />GitHub 导入</button>
            <button type="button" onClick={() => void copyText("loom skill install ./my-skill.zip", "ZIP install command copied.")}><FolderOpen size={14} />ZIP 导入</button>
          </div>
        </div>

        <Section title="Discovery status" caption="View discovered, installed, and active skills.">
          <div className="skills-stat-grid">
            <div className="skills-stat-card"><span>Preference</span><strong>{enabled ? "On" : "Off"}</strong><StatusPill tone={enabled ? "ready" : "off"}>{enabled ? "Active" : "Off"}</StatusPill></div>
            <div className="skills-stat-card"><span>Discovered skills</span><strong>{String(discoveredCount)}</strong><small>Project and user directories</small></div>
            <div className="skills-stat-card"><span>Installed skills</span><strong>{String(installedCount)}</strong><small>&lt;LOOM_HOME&gt;/skills</small></div>
            <div className="skills-stat-card"><span>In use</span><strong>{String(activeSkillCount)}</strong><small>Loaded into context</small></div>
            <div className="skills-stat-card"><span>Discovery health</span><strong>{healthText}</strong><StatusPill tone={errors.length ? "warning" : "ready"}>{errors.length ? "Check" : "Ready"}</StatusPill></div>
          </div>
        </Section>

        <Section title="Skill sources" caption="Skills are discovered from project and user directories.">
          <div className="settings-card skills-source-grid">
            {[
              { icon: FolderOpen, title: "Project source", path: ".agents/skills", detail: "当前仓库或工作区内的团队技能" },
              { icon: Database, title: "User source", path: "~/.agents/skills", detail: "兼容 Codex 风格的本机用户技能" },
              { icon: Sparkles, title: "Loom Home", path: "<LOOM_HOME>/skills", detail: "loom skill install 的默认安装目录" },
            ].map((source) => {
              const Icon = source.icon;
              return <div className="skills-source-card" key={source.path}><span><Icon size={16} /></span><div><strong>{source.title}</strong><code>{source.path}</code><p>{source.detail}</p></div><StatusPill tone="ready">Scan available</StatusPill></div>;
            })}
          </div>
        </Section>

        <Section title="Install and manage" caption="Copy an installation command, then run it in your terminal.">
          <div className="settings-card skills-management-card">
            <div className="skills-command-strip">
              <label><Search size={14} /><input placeholder="搜索已安装技能，例如 pdf / browser / deploy" /></label>
              <button type="button" onClick={() => void copyText("loom skill list", "List command copied.")}>列出技能</button>
              <button type="button" onClick={() => void copyText("loom skill update --all", "Update command copied.")}>更新全部</button>
              <button type="button" onClick={() => void copyText("loom skill remove <name>", "Remove command copied.")}>移除命令</button>
            </div>
            <div className="skills-install-grid">
              <button type="button" onClick={() => void copyText("loom skill install ./my-skill", "Local install command copied.")}><FolderOpen size={16} /><strong>Local directory</strong><span>Requires SKILL.md</span></button>
              <button type="button" onClick={() => void copyText("loom skill install https://github.com/owner/repository/tree/main/path", "GitHub tree command copied.")}><Globe2 size={16} /><strong>GitHub repository</strong><span>HTTPS remote sources only</span></button>
              <button type="button" onClick={() => void copyText("loom skill install ./skill.zip", "ZIP command copied.")}><Database size={16} /><strong>ZIP archive</strong><span>Paths are checked before installation</span></button>
              <button type="button" onClick={() => void copyText("loom skill search <query>", "Search command copied.")}><Search size={16} /><strong>Search skills</strong><span>Find workflows by name or description</span></button>
            </div>
          </div>
        </Section>

        <Section title="运行策略" caption="这些策略让 Skill 保持轻量：先发现，再按需加载，只有需要资源时才读取或暂存 bundle。">
          <div className="settings-card skills-runtime-grid">
            {[
              { icon: BrainCircuit, title: "按需加载", detail: "初始上下文只暴露 metadata，需要时再 skill_load。", value: "开启" },
              { icon: Database, title: "缓存元数据", detail: "发现结果用于设置页和 tool search，避免上下文膨胀。", value: "自动" },
              { icon: Code2, title: "上下文预算", detail: "已加载 skill 快照受 active skill context budget 约束。", value: "32 KB" },
              { icon: FolderOpen, title: "资源读取", detail: "UTF-8 文本资源可单独读取，二进制资源需先暂存。", value: "受控" },
              { icon: Terminal, title: "Bundle 暂存", detail: "复制到 .loom/skill-runs/<name>，暂存不会执行脚本。", value: "审批" },
              { icon: Wrench, title: "脚本执行", detail: "后续 process 调用仍走 PermissionEngine 和沙箱策略。", value: "受限" },
            ].map((item) => {
              const Icon = item.icon;
              return <div className="skills-runtime-item" key={item.title}><span><Icon size={15} /></span><div><strong>{item.title}</strong><p>{item.detail}</p></div><code>{item.value}</code></div>;
            })}
          </div>
        </Section>

        <Section title="安全控制" caption="安装器只复制和校验 bundle，不执行外部代码。这里展示的是当前 main 已启用的保护边界。">
          <div className="settings-card skills-security-grid">
            {[
              "HTTPS remote sources only",
              "安装时不执行脚本",
              "ZIP 路径穿越检查",
              "Symlink 拒绝 / 跳过",
              "Git 非交互模式",
              "file:// 与 ext:: 传输禁用",
            ].map((item) => <div className="skills-security-row" key={item}><ShieldCheck size={15} /><span>{item}</span><StatusPill tone="ready">已保护</StatusPill></div>)}
          </div>
        </Section>

        <Section title="Available skills" caption="Workflows discovered by the current runtime.">
          <div className="settings-card skills-table">
            {installedRows.length === 0 ? <div className="settings-empty-state"><strong>No skills found.</strong><p>Install a skill or add one to a discovery directory.</p></div> : null}
            <div className="skills-table-head"><span>Name</span><span>简介</span><span>来源</span><span>状态</span><span>Actions</span></div>
            {installedRows.map((skill) => <div className="skills-table-row" key={`${skill.name}-${skill.source}`}><div className="skills-name-cell"><Sparkles size={14} /><strong>{skill.name}</strong></div><span>{skill.description}</span><code>{skill.source}</code><StatusPill tone={skill.tone}>{skill.status}</StatusPill><div className="skills-row-actions"><button type="button" onClick={() => void copyText(`loom skill info ${skill.name}`, "Info command copied.")}>查看</button><button type="button" onClick={() => void copyText(`loom skill update ${skill.name}`, "Update command copied.")}>更新</button></div></div>)}
          </div>
        </Section>

        <div className="skills-footer-strip">
          <span><Activity size={14} />上次扫描：运行时上报</span>
          <span><Sparkles size={14} />Skills Runtime v2</span>
          <span><Check size={14} />配置已自动保存</span>
        </div>
      </>
    );
  };

  const renderPermissions = () => (
    <><div className="settings-page-heading"><div><h1>Permissions</h1><p>Review access levels and approval rules.</p></div></div><Section title="Permission profiles" caption="Each conversation can use its own permission mode."><div className="settings-card permission-grid">{(runtime.permissionModes ?? ["approval", "workspace", "full-access"]).map((mode) => <div className={`permission-card ${mode === runtime.defaultPermissionMode ? "selected" : ""}`} key={mode}><ShieldCheck size={18} /><div><strong>{titleCase(mode)}</strong><span>{mode === "full-access" ? "Broad access for a trusted local environment." : mode === "workspace" ? "Keep file operations within the active workspace." : "Ask before sensitive or destructive actions."}</span></div>{mode === runtime.defaultPermissionMode ? <StatusPill tone="ready">Default</StatusPill> : null}</div>)}</div></Section><Section title="Safety boundaries"><div className="settings-card settings-detail-list"><DetailRow label="Computer Use" value={capabilityEnabled("computerUse") ? "Available under permission policy" : "Capability off"} /><DetailRow label="Browser" value={capabilityEnabled("browserUse") ? "Available under permission policy" : "Capability off"} /><DetailRow label="Shell / process" value="Permission-aware" /><DetailRow label="Workspace writes" value="Permission-aware" /></div></Section></>
  );

  const renderShortcuts = () => (
    <KeyboardShortcutsSettings
      shortcuts={mergeShortcutSettings(settings.shortcuts)}
      onChange={saveShortcut}
      onReset={resetShortcut}
      onResetAll={resetShortcuts}
    />
  );

  const renderPrivacy = () => {
    const prefs = { ...DEFAULT_PRIVACY, ...(settings.privacy ?? {}) } as PrivacySettings;
    return <><div className="settings-page-heading"><div><h1>Privacy & data</h1><p>Manage diagnostics preferences and local data.</p></div></div><Section title="Diagnostics preferences"><div className="settings-card mature-preference-list"><PreferenceRow icon={Activity} title="Usage telemetry" detail="Consent for future anonymous usage metrics. Off by default."><SettingSwitch checked={prefs.telemetry} label="Usage telemetry" onChange={(value) => void saveSetting("privacy.telemetry", value)} /></PreferenceRow><PreferenceRow icon={CircleAlert} title="Crash reports" detail="Consent for future crash reports. Off by default."><SettingSwitch checked={prefs.crashReports} label="Crash reports" onChange={(value) => void saveSetting("privacy.crashReports", value)} /></PreferenceRow></div></Section><Section title="Local storage"><div className="settings-card settings-detail-list"><DetailRow label="Runtime home" value="Loom local runtime directory" detail="Stored locally unless a connected service sends data elsewhere." /><DetailRow label="Settings store" value="settings.json" detail="Saved capability and interface preferences." /><DetailRow label="Renderer fallback" value="localStorage" detail="Keeps interface preferences available while the runtime is offline." /></div></Section><div className="settings-callout"><Info size={16} /><div><strong>No telemetry transport is enabled by these switches today.</strong><span>They are explicit persisted consent preferences for future diagnostics; the current Loom runtime stays local-first.</span></div></div></>;
  };

  const renderDeveloper = () => {
    const diagnostics = JSON.stringify({ runtime, settings, models: modelState }, null, 2);
    return <><div className="settings-page-heading settings-heading-with-action"><div><h1>Developer</h1><p>Inspect runtime status and copy diagnostics.</p></div><button className="mature-action-button" type="button" onClick={() => void copyText(diagnostics, "Diagnostics copied.")}><Copy size={14} />Copy diagnostics</button></div><Section title="Runtime diagnostics"><div className="settings-card settings-detail-list"><DetailRow label="Registered tools" value={String(runtime.registeredToolCount ?? "—")} /><DetailRow label="Exposed tools" value={String(runtime.exposedToolCount ?? "—")} /><DetailRow label="Settings schema" value={String(settings.schemaVersion ?? 1)} /><DetailRow label="Image attachments" value={runtime.attachments?.images === false ? "Disabled" : "Enabled"} /><DetailRow label="File attachments" value={runtime.attachments?.files === false ? "Disabled" : "Enabled"} /></div></Section><Section title="Integration summary"><div className="settings-card settings-detail-list"><DetailRow label="Web Search" value={text(webStatus?.provider, capabilityStatusText(webStatus))} /><DetailRow label="MCP servers" value={String(mcpStatus?.connected_servers ?? "Not reported")} /><DetailRow label="MCP tools" value={String(mcpStatus?.tool_count ?? "Not reported")} /><DetailRow label="Computer Use" value={capabilityStatusText(computerStatus)} /><DetailRow label="Browser Use" value={capabilityStatusText(browserStatus)} /></div></Section><Section title="Raw snapshot" caption="Useful when comparing renderer state with App Server state."><pre className="mature-diagnostics-preview">{diagnostics.slice(0, 6500)}</pre></Section></>;
  };

  // Opened, settings paints its frame (the sidebar, the empty page) in the frame after the click and the page itself a
  // frame later, while it is already fading in: the first frame used to wait for the whole page (~50-120 ms of render,
  // style and layout).
  const bodyPainted = usePaintedOnce();
  const content = !bodyPainted ? null : (() => {
    if (page === "general") return renderGeneral();
    if (page === "ambient") return <AmbientSettingsPanel threadId={threadId} />;
    if (page === "projectRuns") return <ProjectRunsPanel threadId={threadId} onOpenThread={onOpenThread} />;
    if (page === "appearance") return renderAppearance();
    if (page === "models") return renderModels();
    if (page === "capabilities") return renderCapabilities();
    if (page === "computer") return renderComputer();
    if (page === "browser") return renderBrowser();
    if (page === "websearch") return renderWebSearch();
    if (page === "terminal") return renderTerminal();
    if (page === "plugins") return renderPlugins();
    if (page === "connectors") return <div className="settings-connectors-page"><ConnectorsSettings running={running} /><ConnectorLifecycleStatus /></div>;
    if (page === "mcp") return renderMcp();
    if (page === "skills") return renderSkills();
    if (page === "memory") return <MemoryPanel threadId={threadId} running={running} />;
    if (page === "permissions") return renderPermissions();
    if (page === "shortcuts") return renderShortcuts();
    if (page === "privacy") return renderPrivacy();
    return renderDeveloper();
  })();

  return (
    <div className="settings-shell settings-refined">
      <aside className="settings-sidebar">
        <div className="settings-sidebar-top">
          <button type="button" className="settings-back" onClick={onClose}><ArrowLeft size={15} /><span>Back to Loom</span></button>
          <div className="settings-sidebar-title"><span className="settings-brand-orb"><Settings size={18} strokeWidth={1.6} /></span><div><strong>Settings</strong><span>Local agent controls</span></div></div>
          <label className="settings-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search settings" /></label>
        </div>
        <nav className="settings-nav" data-ic-row aria-label="Settings navigation" ref={navRef}>
          {filteredGroups.map((group) => <section key={group.label}><span className="settings-nav-label">{group.label}</span>{group.items.map((item) => { const Icon = item.icon; return <button type="button" key={item.key} data-nav={item.key} className={page === item.key ? "active" : ""} aria-current={page === item.key ? "page" : undefined} onClick={() => navigateToPage(item.key)}><i className="settings-nav-pip" aria-hidden="true" /><Icon size={16} strokeWidth={1.7} /><span>{item.label}</span></button>; })}</section>)}
          {filteredGroups.length === 0 ? <div className="settings-nav-empty"><Search size={15} strokeWidth={1.7} /><span>No matching settings</span></div> : null}
        </nav>
        <div className="settings-sidebar-footer" data-running={running ? "true" : "false"}><span className="settings-runtime-dot" /><div><strong>Loom runtime</strong><span>{running ? "Turn active" : "Ready for changes"}</span></div></div>
      </aside>
      <main className="settings-main"><div className="settings-main-scroll" ref={settingsScrollRef}><div className="settings-content" data-settings-page={page}><div className="settings-page-surface" data-page={page} ref={surfaceRef}><Fragment key={page}>{content}</Fragment></div></div></div></main>
      {noticePresence.mounted && visibleNotice ? (
        <div className={`settings-toast ${visibleNotice.tone}`} data-motion-phase={noticePresence.phase}>
          {visibleNotice.tone === "success" ? <Check size={15} /> : <CircleAlert size={15} />}
          <span>{visibleNotice.text}</span>
          <i className="settings-toast-timer" key={visibleNotice.id} aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}

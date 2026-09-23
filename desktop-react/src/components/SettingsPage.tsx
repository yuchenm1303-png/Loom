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
  Monitor,
  Moon,
  Palette,
  Plug,
  RefreshCw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Sun,
  Terminal,
  Type,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DEFAULT_SHORTCUTS,
  SHORTCUTS_CHANGED_EVENT,
  mergeShortcutSettings,
  type ShortcutCommandId,
  type ShortcutSettings,
} from "../keyboardShortcuts";
import { applyThemePreference, type LoomThemePreference } from "../theme";
import { useMotionPresence } from "../motion/useMotionPresence";
import type {
  InitializeResult,
  LoomSettings,
  ModelSnapshot,
} from "../types/loom";
import { KeyboardShortcutsSettings } from "./KeyboardShortcutsSettings";
import { ModelsSettingsPanel } from "./ModelsSettingsPanel";
import "./settings-page.css";
import "./settings-general-polish.css";
import "./settings-maturity.css";
import "./settings-appearance.css";
import "./settings-terminal.css";

type PageKey =
  | "general"
  | "appearance"
  | "models"
  | "capabilities"
  | "computer"
  | "browser"
  | "terminal"
  | "plugins"
  | "mcp"
  | "skills"
  | "permissions"
  | "shortcuts"
  | "privacy"
  | "developer";

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

type BrowserConnectionMode = "auto" | "local-launch" | "cdp-attach" | "extension";

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

type PluginRecord = {
  name?: string;
  version?: string;
  enabled?: boolean;
  status?: string;
  source?: string;
  description?: string;
};

type BrowserExtensionSetupResult = Awaited<ReturnType<Window["loom"]["setupBrowserExtension"]>> & {
  browser: "edge" | "chrome";
};

interface SettingsPageProps {
  runtime: RuntimeView;
  models: ModelSnapshot | null;
  running: boolean;
  onClose(): void;
}

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

const LOCAL_DESKTOP_SETTINGS_KEY = "loom.settings.desktop.v2";
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
  theme: "system",
  scale: "100",
  density: "comfortable",
  reducedMotion: false,
  conversationWidth: "balanced",
  sidebarWidth: "standard",
  inspectorWidth: "standard",
  chatFontSize: 13,
  messageLineHeight: "comfortable",
  ambientEffects: true,
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
    detail: "Use your current Edge/Chrome tab. If the bridge is offline, stop and report it; never switch browser identity.",
  },
  {
    value: "extension",
    label: "Current browser only",
    detail: "Require the Loom extension and drive the tab you are looking at. Never fall back to another browser.",
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
      { key: "appearance", label: "Appearance", icon: Palette },
      { key: "models", label: "Models", icon: Cpu },
      { key: "capabilities", label: "Capabilities", icon: Blocks },
      { key: "permissions", label: "Permissions", icon: ShieldCheck },
    ],
  },
  {
    label: "Integrations",
    items: [
      { key: "computer", label: "Computer Use", icon: Monitor },
      { key: "browser", label: "Browser", icon: Globe2 },
      { key: "terminal", label: "Terminal", icon: Terminal },
      { key: "plugins", label: "Plugins", icon: Plug },
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

const CAPABILITIES: {
  key: CapabilityKey;
  title: string;
  description: string;
  icon: LucideIcon;
  detailPage?: PageKey;
}[] = [
  { key: "computerUse", title: "Computer Use", description: "Expose screenshot, UIA, mouse, and keyboard tools.", icon: Monitor, detailPage: "computer" },
  { key: "browserUse", title: "Browser Use", description: "Drive the current browser, an isolated browser, or a local CDP target.", icon: Globe2, detailPage: "browser" },
  { key: "webSearch", title: "Web Search", description: "Expose the configured public-web search provider.", icon: Search },
  { key: "mcp", title: "MCP tools", description: "Expose tools discovered from configured MCP servers.", icon: Plug, detailPage: "mcp" },
  { key: "skills", title: "Skills", description: "Discover and load reusable SKILL.md workflows.", icon: Sparkles, detailPage: "skills" },
  { key: "toolSearch", title: "Tool Search", description: "Discover deferred integration tools on demand.", icon: Wrench },
  { key: "codeMode", title: "Code Mode", description: "Allow bounded multi-tool composition in Loom's execution language.", icon: Code2 },
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
    window.localStorage.setItem(LOCAL_DESKTOP_SETTINGS_KEY, JSON.stringify(settings));
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
  document.documentElement.dataset.loomCodeWrap = String(appearance.codeWrap);
  document.documentElement.style.setProperty("--content-width", CONVERSATION_WIDTH_VALUES[appearance.conversationWidth] ?? CONVERSATION_WIDTH_VALUES.balanced);
  document.documentElement.style.setProperty("--sidebar-width", SIDEBAR_WIDTH_VALUES[appearance.sidebarWidth] ?? SIDEBAR_WIDTH_VALUES.standard);
  document.documentElement.style.setProperty("--inspector-width", INSPECTOR_WIDTH_VALUES[appearance.inspectorWidth] ?? INSPECTOR_WIDTH_VALUES.standard);
  document.documentElement.style.setProperty("--loom-chat-font-size", `${appearance.chatFontSize}px`);
  document.documentElement.style.setProperty("--loom-message-line-height", MESSAGE_LINE_HEIGHT_VALUES[appearance.messageLineHeight] ?? MESSAGE_LINE_HEIGHT_VALUES.comfortable);
  const mono = appearance.codeFont === "system"
    ? "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"
    : `${appearance.codeFont}, ui-monospace, monospace`;
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

function StatusPill({ tone, children }: { tone: string; children: string }) {
  return <span className={`settings-status-pill ${tone}`}><span className="settings-status-dot" />{children}</span>;
}

function Section({ title, caption, children }: { title: string; caption?: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <div className="settings-section-heading"><h2>{title}</h2>{caption ? <p>{caption}</p> : null}</div>
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

export function SettingsPage({ runtime, models, running, onClose }: SettingsPageProps) {
  const [page, setPage] = useState<PageKey>("general");
  const [query, setQuery] = useState("");
  const [settings, setSettings] = useState<DesktopSettings>(() => mergedSettings(runtime));
  const [modelState, setModelState] = useState<ModelSnapshot | null>(models);
  const [busyCapability, setBusyCapability] = useState<CapabilityKey | null>(null);
  const [notice, setNotice] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const noticePresence = useMotionPresence(Boolean(notice), 150);
  const lastNoticeRef = useRef<{ tone: "error" | "success"; text: string } | null>(notice);
  if (notice) lastNoticeRef.current = notice;
  const visibleNotice = notice ?? lastNoticeRef.current;
  const [plugins, setPlugins] = useState<PluginRecord[] | null>(null);
  const [pluginsError, setPluginsError] = useState("");
  const [browserSetupBusy, setBrowserSetupBusy] = useState(false);
  const [browserSetup, setBrowserSetup] = useState<BrowserExtensionSetupResult | null>(null);
  const [browserConnectionCheck, setBrowserConnectionCheck] = useState<"idle" | "checking" | "connected" | "offline">("idle");
  // Held locally so the endpoint can be typed without a round trip per keystroke.
  const [cdpDraft, setCdpDraft] = useState(settings.browser?.cdpUrl ?? DEFAULT_BROWSER.cdpUrl);

  useEffect(() => {
    const merged = mergedSettings(runtime);
    setSettings((current) => ({ ...merged, ...current, capabilities: { ...merged.capabilities, ...current.capabilities } }));
  }, [runtime.settings]);

  useEffect(() => {
    setModelState(models);
  }, [models]);

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
          capabilities: { ...DEFAULT_CAPABILITIES, ...(result.settings.capabilities ?? {}), ...(local.capabilities ?? {}) },
          appearance: { ...DEFAULT_APPEARANCE, ...(result.settings.appearance ?? {}), ...(local.appearance ?? {}) },
          shortcuts: mergeShortcutSettings({ ...(result.settings.shortcuts ?? {}), ...(local.shortcuts ?? {}) }),
          terminal: { ...DEFAULT_TERMINAL, ...(result.settings.terminal ?? {}), ...(local.terminal ?? {}) },
          browser: { ...DEFAULT_BROWSER, ...(result.settings.browser ?? {}), ...(local.browser ?? {}) },
          computer: { ...DEFAULT_COMPUTER, ...(result.settings.computer ?? {}), ...(local.computer ?? {}) },
          privacy: { ...DEFAULT_PRIVACY, ...(result.settings.privacy ?? {}), ...(local.privacy ?? {}) },
        } as DesktopSettings;
        setSettings(next);
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

  useEffect(() => {
    if (page !== "plugins" || plugins !== null || pluginsError) return;
    let disposed = false;
    void window.loom.call<{ plugins?: PluginRecord[] }>("plugin/list", {})
      .then((result) => { if (!disposed) setPlugins(Array.isArray(result.plugins) ? result.plugins : []); })
      .catch((cause) => { if (!disposed) setPluginsError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { disposed = true; };
  }, [page, plugins, pluginsError]);

  const filteredGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return NAV_GROUPS;
    return NAV_GROUPS
      .map((group) => ({ ...group, items: group.items.filter((item) => item.label.toLowerCase().includes(needle)) }))
      .filter((group) => group.items.length);
  }, [query]);

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
    return (
      <>
        <div className="settings-page-heading general-page-heading">
          <div><span className="settings-eyebrow">Loom desktop</span><h1>General</h1><p>Core defaults, runtime health, and the fastest routes to the settings that shape every agent run.</p></div>
          <div className={`general-heading-status ${running ? "running" : ""}`}><i /><span>{running ? "Agent turn active" : "Runtime ready"}</span></div>
        </div>

        <div className="general-overview-card">
          <div className="general-overview-copy"><div className="general-overview-icon"><Activity size={19} /></div><strong>{running ? "Loom is working" : "Local runtime ready"}</strong><p>Model, permission boundary, exposed tools, and capability state at a glance.</p></div>
          <div className="general-stat-grid">
            <div className="general-stat"><Cpu size={17} /><div><span>Model</span><strong>{modelLabel}</strong></div></div>
            <div className="general-stat"><ShieldCheck size={17} /><div><span>Permission</span><strong>{permissionLabel}</strong></div></div>
            <div className="general-stat"><Wrench size={17} /><div><span>Tools</span><strong>{runtime.exposedToolCount ?? "—"} / {runtime.registeredToolCount ?? "—"}</strong></div></div>
            <div className="general-stat"><Blocks size={17} /><div><span>Capabilities</span><strong>{enabledCapabilities} / {CAPABILITIES.length} enabled</strong></div></div>
          </div>
        </div>

        <Section title="Runtime defaults" caption="Defaults currently reported by the App Server for new conversations.">
          <div className="general-default-grid">
            <div className="general-default-card"><div className="general-default-card-head"><span className="general-default-card-icon"><FolderOpen size={16} /></span></div><label>Default workspace</label><strong title={text(runtime.defaultWorkspace)}>{text(runtime.defaultWorkspace)}</strong><p>Used when a new conversation starts without an explicit project.</p><div className="general-workspace-actions"><button type="button" className="general-copy-button" onClick={() => void copyText(text(runtime.defaultWorkspace, ""), "Workspace path copied.")}><Copy size={13} />Copy path</button></div></div>
            <button type="button" className="general-default-card" onClick={() => setPage("permissions")}><div className="general-default-card-head"><span className="general-default-card-icon"><ShieldCheck size={16} /></span><ChevronRight size={15} /></div><label>Default permission</label><strong>{permissionLabel}</strong><p>Inspect the execution boundary used for sensitive actions.</p></button>
            <button type="button" className="general-default-card" onClick={() => setPage("models")}><div className="general-default-card-head"><span className="general-default-card-icon"><Cpu size={16} /></span><ChevronRight size={15} /></div><label>Current model</label><strong>{modelLabel}</strong><p>Switch the active inference profile and inspect providers.</p></button>
          </div>
        </Section>

        <Section title="Quick access" caption="The controls most likely to change how Loom behaves.">
          <div className="general-quick-grid">
            <button type="button" className="general-quick-card" onClick={() => setPage("appearance")}><Palette size={18} /><div><strong>Appearance</strong><span>Scale, layout, reading rhythm, motion, and code typography</span></div><ChevronRight size={15} /></button>
            <button type="button" className="general-quick-card" onClick={() => setPage("capabilities")}><Blocks size={18} /><div><strong>Capabilities</strong><span>Choose the tool families Loom can expose</span></div><ChevronRight size={15} /></button>
            <button type="button" className="general-quick-card" onClick={() => setPage("developer")}><Wrench size={18} /><div><strong>Diagnostics</strong><span>Runtime, integrations, and raw health snapshot</span></div><ChevronRight size={15} /></button>
          </div>
        </Section>
      </>
    );
  };

  const renderAppearance = () => {
    const appearance = { ...DEFAULT_APPEARANCE, ...(settings.appearance ?? {}) } as AppearanceSettings;
    const densityLabel = titleCase(appearance.density);
    const widthLabel = titleCase(appearance.conversationWidth);
    const themeLabel = appearance.theme === "system" ? "System theme" : `${titleCase(appearance.theme)} theme`;
    return (
      <>
        <div className="settings-page-heading settings-heading-with-action">
          <div><span className="settings-eyebrow">Presentation</span><h1>Appearance</h1><p>Tune Loom for long reading sessions, dense agent work, or a wider desktop layout without changing Agent behavior.</p></div>
          <button className="mature-action-button" type="button" onClick={() => void resetAppearance()}><RefreshCw size={14} />Reset appearance</button>
        </div>

        <div className="appearance-overview-card">
          <div className="appearance-overview-copy">
            <span className="settings-eyebrow">Live workspace</span>
            <strong>{appearance.scale}% scale · {densityLabel} density</strong>
            <p>Layout, message typography, motion, and code preferences update immediately. The preview reflects the current reading profile.</p>
            <div className="appearance-overview-meta"><span>{themeLabel}</span><span>{widthLabel} conversation</span><span>{appearance.chatFontSize}px chat text</span><span>{appearance.ambientEffects ? "Ambient on" : "Ambient off"}</span></div>
          </div>
          <div className="appearance-workspace-preview" aria-hidden="true">
            <div className="appearance-preview-sidebar" />
            <div className="appearance-preview-main"><span /><span /><span /></div>
            <div className="appearance-preview-inspector" />
          </div>
        </div>

        <Section title="Theme" caption="Choose Loom's overall surface palette. System follows your operating system and updates automatically.">
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
                  </span>
                  <span className="appearance-theme-option-copy">
                    <span className="appearance-theme-option-title">
                      <Icon size={15} strokeWidth={1.8} />
                      <strong>{option.label}</strong>
                      {active ? <Check size={14} className="appearance-theme-check" /> : null}
                    </span>
                    <span>{option.detail}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </Section>

        <Section title="Interface" caption="Global sizing and motion preferences. Changes apply immediately and persist across restarts.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Type} title="Interface scale" detail="Scale the complete desktop UI for comfortable reading on small or high-DPI displays."><div className="appearance-segmented">{(["90", "100", "110", "120", "130"] as const).map((scale) => <button type="button" key={scale} className={appearance.scale === scale ? "active" : ""} onClick={() => void saveSetting("appearance.scale", scale, `Interface scale set to ${scale}%.`)}>{scale}%</button>)}</div></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Content density" detail="Control spacing across conversation, activity, tool, and settings rows."><SelectControl label="Content density" value={appearance.density} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "spacious", label: "Spacious" }]} onChange={(value) => void saveSetting("appearance.density", value)} /></PreferenceRow>
            <PreferenceRow icon={Moon} title="Reduce motion" detail="Minimize decorative transitions, pulses, and status animation."><SettingSwitch checked={appearance.reducedMotion} label="Reduce motion" onChange={(value) => void saveSetting("appearance.reducedMotion", value)} /></PreferenceRow>
            <PreferenceRow icon={Sparkles} title="Ambient effects" detail="Show the subtle conversation glow and background atmosphere behind messages."><SettingSwitch checked={appearance.ambientEffects} label="Ambient conversation effects" onChange={(value) => void saveSetting("appearance.ambientEffects", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="Workspace layout" caption="Shape the three-column desktop workspace without changing any project or conversation data.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Monitor} title="Conversation width" detail="Set the maximum width of the central reading column."><SelectControl label="Conversation width" value={appearance.conversationWidth} options={[{ value: "focused", label: "Focused · 740px" }, { value: "balanced", label: "Balanced · 860px" }, { value: "wide", label: "Wide · 1040px" }]} onChange={(value) => void saveSetting("appearance.conversationWidth", value)} /></PreferenceRow>
            <PreferenceRow icon={Monitor} title="Sidebar width" detail="Choose how much room projects and conversation titles receive."><SelectControl label="Sidebar width" value={appearance.sidebarWidth} options={[{ value: "compact", label: "Compact · 220px" }, { value: "standard", label: "Standard · 252px" }, { value: "wide", label: "Wide · 292px" }]} onChange={(value) => void saveSetting("appearance.sidebarWidth", value)} /></PreferenceRow>
            <PreferenceRow icon={Monitor} title="Inspector width" detail="Control the space reserved for runtime details and tool activity."><SelectControl label="Inspector width" value={appearance.inspectorWidth} options={[{ value: "compact", label: "Compact · 280px" }, { value: "standard", label: "Standard · 316px" }, { value: "wide", label: "Wide · 360px" }]} onChange={(value) => void saveSetting("appearance.inspectorWidth", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="Conversation reading" caption="Tune message typography independently from the rest of the desktop UI.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Type} title="Chat text size" detail="Adjust user and assistant message text without scaling the surrounding controls."><SelectControl label="Chat text size" value={String(appearance.chatFontSize)} options={[12, 13, 14, 15, 16, 17].map((value) => ({ value: String(value), label: `${value}px` }))} onChange={(value) => void saveSetting("appearance.chatFontSize", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Message line spacing" detail="Control vertical rhythm for long assistant responses and Markdown paragraphs."><SelectControl label="Message line spacing" value={appearance.messageLineHeight} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "relaxed", label: "Relaxed" }]} onChange={(value) => void saveSetting("appearance.messageLineHeight", value)} /></PreferenceRow>
          </div>
          <div className="appearance-reading-preview">
            <span className="appearance-reading-preview-label">Message preview</span>
            <div className="appearance-reading-preview-message"><strong>Loom</strong> keeps the response column readable while preserving the same Agent behavior. This preview uses your current chat text size and line spacing, so you can tune long-form answers before returning to the conversation.</div>
          </div>
        </Section>

        <Section title="Code appearance" caption="Monospace settings affect code blocks, terminal-style previews, and technical values.">
          <div className="settings-card mature-preference-list">
            <PreferenceRow icon={Code2} title="Code font" detail="Use the system monospace stack or a font installed on this machine."><input className="mature-input" value={appearance.codeFont} onChange={(event) => setSettings(setNestedSetting(settings, "appearance.codeFont", event.target.value))} onBlur={(event) => void saveSetting("appearance.codeFont", event.target.value.trim() || "system")} placeholder="system or JetBrains Mono" /></PreferenceRow>
            <PreferenceRow icon={Type} title="Code font size" detail="Adjust code and terminal-style output independently from conversation text."><SelectControl label="Code font size" value={String(appearance.codeFontSize)} options={[10, 11, 12, 13, 14, 15, 16, 17, 18].map((value) => ({ value: String(value), label: `${value}px` }))} onChange={(value) => void saveSetting("appearance.codeFontSize", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="Code line spacing" detail="Choose a tighter terminal feel or more room between long source lines."><SelectControl label="Code line spacing" value={appearance.codeLineHeight} options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "relaxed", label: "Relaxed" }]} onChange={(value) => void saveSetting("appearance.codeLineHeight", value)} /></PreferenceRow>
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
      <div className="settings-page-heading"><div><span className="settings-eyebrow">Agent runtime</span><h1>Capabilities</h1><p>Choose which major tool families Loom can expose to the model.</p></div><span className="settings-tool-count">{runtime.exposedToolCount ?? "—"} / {runtime.registeredToolCount ?? "—"} tools exposed</span></div>
      {running ? <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Finish or stop the active turn first.</strong><span>Tool exposure cannot change mid-execution.</span></div></div> : null}
      <Section title="Agent capabilities" caption="Preference and runtime readiness are intentionally shown separately.">
        <div className="settings-card capability-list">
          {CAPABILITIES.map((item) => {
            const Icon = item.icon;
            const enabled = capabilityEnabled(item.key);
            const status = statusFor(item.key);
            const provider = item.key === "webSearch" && enabled ? text(status?.provider, "") : "";
            const baseBadge = capabilityLabel(status, enabled);
            const badge = provider ? { ...baseBadge, text: `${baseBadge.text} · ${titleCase(provider)}` } : baseBadge;
            const description = provider ? `${item.description} Active provider: ${titleCase(provider)}.` : item.description;
            return <div className="capability-row" key={item.key}><div className="capability-icon"><Icon size={17} /></div><div className="capability-copy"><div className="capability-title-line"><strong>{item.title}</strong><StatusPill tone={badge.tone}>{badge.text}</StatusPill></div><span>{description}</span></div>{item.detailPage ? <button type="button" className="settings-row-link" onClick={() => setPage(item.detailPage!)}><ChevronRight size={15} /></button> : <span className="settings-row-link-spacer" />}<SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label={`Toggle ${item.title}`} onChange={(value) => void setCapability(item.key, value)} /></div>;
          })}
        </div>
      </Section>
    </>
  );

  const renderComputer = () => {
    const enabled = capabilityEnabled("computerUse");
    const badge = capabilityLabel(computerStatus, enabled);
    const prefs = { ...DEFAULT_COMPUTER, ...(settings.computer ?? {}) } as ComputerSettings;
    return <><div className="settings-page-heading settings-heading-with-switch"><div><span className="settings-eyebrow">Desktop integration</span><h1>Computer Use</h1><p>Screenshot-driven Windows control with UI Automation assistance and post-action verification.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Computer Use" onChange={(value) => void setCapability("computerUse", value)} /></div></div>
      <Section title="Runtime status"><div className="settings-card settings-detail-list"><DetailRow label="Backend" value={capabilityStatusText(computerStatus)} /><DetailRow label="Operator" value={text(computerStatus?.operator, "Not reported")} /><DetailRow label="Grounder" value={text(computerStatus?.grounder, "Not reported")} /><DetailRow label="Observation" value={text(computerStatus?.observation_mode, "Not reported")} /><DetailRow label="Verification" value={text(computerStatus?.verification, "Not reported")} /></div></Section>
      <Section title="Desktop preferences" caption="Durable preferences for the Computer Use runtime."><div className="settings-card mature-preference-list"><PreferenceRow icon={ShieldCheck} title="Verify actions" detail="Prefer post-action verification before the agent moves on."><SettingSwitch checked={prefs.verifyActions} label="Verify Computer Use actions" onChange={(value) => void saveSetting("computer.verifyActions", value)} /></PreferenceRow><PreferenceRow icon={Monitor} title="Screenshot quality" detail="Balance grounding detail against capture and transfer cost."><SelectControl label="Screenshot quality" value={prefs.screenshotQuality} options={[{ value: "fast", label: "Fast" }, { value: "balanced", label: "Balanced" }, { value: "high", label: "High detail" }]} onChange={(value) => void saveSetting("computer.screenshotQuality", value)} /></PreferenceRow></div></Section>
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
    return <><div className="settings-page-heading settings-heading-with-switch"><div><span className="settings-eyebrow">Web interaction</span><h1>Browser</h1><p>Drive your current signed-in Edge/Chrome tab by default, or explicitly choose a clean isolated browser.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Browser Use" onChange={(value) => void setCapability("browserUse", value)} /></div></div>
      <Section title="Browser runtime"><div className="settings-card settings-detail-list"><DetailRow label="Requested backend" value={requestedConnection} /><DetailRow label="Actual backend" value={selectedConnection} /><DetailRow label="Backend" value={text(browserStatus?.backend, capabilityStatusText(browserStatus))} /><DetailRow label="Current browser bridge" value={extensionConnected ? "Connected" : "Not connected"} /><DetailRow label="Browser" value={text(bridge.browser, extensionConnected ? "Chromium browser" : "Not connected")} /><DetailRow label="Current tab" value={text(currentTab.title, currentTab.url ? String(currentTab.url) : "Not reported")} /><DetailRow label="Active sessions" value={String(browserStatus?.active_sessions ?? "Not reported")} /></div></Section>
      <div className={`settings-callout ${extensionConnected ? "" : "warning"}`}>{extensionConnected ? <Check size={16} /> : <CircleAlert size={16} />}<div><strong>{extensionConnected ? "Current browser is connected." : "Current Browser Bridge is offline."}</strong><span>{extensionConnected ? "The next current-browser session uses this browser's active tab, cookies, and login state." : "Loom will stop current-browser tasks instead of opening an isolated browser or using Computer Use."}</span></div></div>
      <Section title="Current Browser extension" caption="Loom prepares a paired local extension. Browser store publishing is not required for development or local installs.">
        <div className="settings-card mature-preference-list">
          <PreferenceRow icon={Plug} title="Install or update" detail="One click updates an existing bridge automatically. The browser requires one confirmation only for the first local installation.">
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
      <Section title="Which browser Loom drives" caption="Current browser is the safe default. Every backend switch is explicit and status always reports the actual identity.">
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
          <PreferenceRow icon={ShieldAlert} title="Let the model pick the browser" detail="The model may choose an external current-tab/CDP backend only when permitted. A clean isolated browser still requires explicit task intent.">
            <SettingSwitch checked={prefs.modelSelectsConnection} label="Model-selected browser connections" onChange={(value) => void saveBrowserSetting("browser.modelSelectsConnection", value, value ? "The model can now choose the browser." : "The model is restricted to the connection above.")} />
          </PreferenceRow>
          <PreferenceRow icon={ShieldAlert} title="Reach local addresses" detail="Let the browser open localhost and private network addresses, such as your own dev server. Off by default because it also reaches services that were never exposed.">
            <SettingSwitch checked={prefs.allowPrivateNetworks} label="Allow private network addresses" onChange={(value) => void saveBrowserSetting("browser.allowPrivateNetworks", value, value ? "The browser can now reach local addresses." : "Local addresses are blocked again.")} />
          </PreferenceRow>
          {prefs.modelSelectsConnection ? (
            <div className="settings-callout-inline"><ShieldAlert size={15} /><span>A browser you are signed into exposes those sessions to the model, and any page it opens can try to redirect it. Remote endpoints stay blocked: attaching is limited to 127.0.0.1 and ::1.</span></div>
          ) : null}
        </div>
      </Section>
      <Section title="Browser preferences"><div className="settings-card mature-preference-list"><PreferenceRow icon={Globe2} title="Preferred isolated browser" detail={prefs.mode === "local-launch" ? "Which installed browser Loom launches for the isolated session." : "Applies only when an isolated browser is explicitly requested."}><SelectControl label="Preferred browser" value={prefs.preferredEngine} options={[{ value: "edge", label: "Microsoft Edge" }, { value: "chrome", label: "Google Chrome" }, { value: "system", label: "System default" }]} onChange={(value) => void saveBrowserSetting("browser.preferredEngine", value, "Preferred isolated browser updated.")} /></PreferenceRow><PreferenceRow icon={Database} title="Persist isolated sessions" detail="Keep cookies and site storage only in Loom's isolated browser profile. Your current browser keeps its own profile automatically."><SettingSwitch checked={prefs.persistSessions} label="Persist isolated browser sessions" onChange={(value) => void saveBrowserSetting("browser.persistSessions", value, value ? "Isolated browser sessions will persist." : "Isolated browser sessions will be ephemeral.")} /></PreferenceRow></div></Section>
    </>;
  };

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
      { name: "项目状态", command: "git status --short", detail: "快速确认工作区是否干净" },
      { name: "类型检查", command: "npm run typecheck", detail: "前端改动后的第一道检查" },
      { name: "生产构建", command: "npm run build", detail: "确认桌面端前端可打包" },
      { name: "技能列表", command: "loom skill list", detail: "查看当前可发现的 Skill" },
    ];

    return (
      <>
        <div className="settings-page-heading terminal-page-heading">
          <div>
            <span className="settings-eyebrow">执行环境</span>
            <h1>终端</h1>
            <p>管理面向 Shell 集成与命令执行界面的持久化偏好、会话行为、安全边界和常用命令模板。</p>
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
              <p>终端偏好集中保存，后续 Shell、Process Runtime、Browser/Computer 调试命令都可以共享这一套默认值。</p>
            </div>
          </div>
          <div className="terminal-quick-actions">
            <button type="button" onClick={() => void copyText("npm run typecheck", "Typecheck command copied.")}><Code2 size={14} />类型检查</button>
            <button type="button" onClick={() => void copyText("npm run build", "Build command copied.")}><Wrench size={14} />构建</button>
            <button type="button" onClick={() => void copyText(text(runtime.defaultWorkspace, ""), "Workspace path copied.")}><FolderOpen size={14} />工作区</button>
          </div>
        </div>

        <Section title="运行概览" caption="把默认 Shell、编码、超时、后台进程和权限边界放在同一屏，方便检查。">
          <div className="terminal-stat-grid">
            <div className="terminal-stat-card"><span>首选 Shell</span><strong>{shellLabel}</strong><small>desktop preference</small></div>
            <div className="terminal-stat-card"><span>文本编码</span><strong>{prefs.encoding === "utf-8" ? "UTF-8" : "System"}</strong><small>stdout / stderr</small></div>
            <div className="terminal-stat-card"><span>命令超时</span><strong>{prefs.commandTimeoutSeconds}s</strong><small>foreground commands</small></div>
            <div className="terminal-stat-card"><span>后台进程</span><strong>{prefs.preserveBackgroundProcesses ? "保留" : "关闭"}</strong><small>managed processes</small></div>
            <div className="terminal-stat-card"><span>权限模式</span><strong>{permissionLabel}</strong><small>permission-aware</small></div>
          </div>
        </Section>

        <Section title="命令默认值" caption="集中保存，供多个运行时集成共享同一套桌面偏好。">
          <div className="settings-card mature-preference-list terminal-preference-card">
            <PreferenceRow icon={Terminal} title="首选 Shell" detail="支持桌面默认值的集成优先采用此 Shell。"><SelectControl label="Preferred shell" value={prefs.shell} options={[{ value: "powershell", label: "PowerShell" }, { value: "cmd", label: "Command Prompt" }, { value: "git-bash", label: "Git Bash" }, { value: "wsl", label: "WSL" }]} onChange={(value) => void saveSetting("terminal.shell", value)} /></PreferenceRow>
            <PreferenceRow icon={Code2} title="文本编码" detail="终端界面和命令输出的默认文本编码偏好。"><SelectControl label="Terminal encoding" value={prefs.encoding} options={[{ value: "utf-8", label: "UTF-8" }, { value: "system", label: "System default" }]} onChange={(value) => void saveSetting("terminal.encoding", value)} /></PreferenceRow>
            <PreferenceRow icon={Gauge} title="命令超时" detail="前台命令集成的首选最长等待时间。"><SelectControl label="Command timeout" value={String(prefs.commandTimeoutSeconds)} options={[30, 60, 120, 300, 600, 1800].map((value) => ({ value: String(value), label: `${value}s` }))} onChange={(value) => void saveSetting("terminal.commandTimeoutSeconds", Number(value))} /></PreferenceRow>
            <PreferenceRow icon={Activity} title="后台进程" detail="运行时支持时保留受管后台进程。"><SettingSwitch checked={prefs.preserveBackgroundProcesses} label="Preserve background processes" onChange={(value) => void saveSetting("terminal.preserveBackgroundProcesses", value)} /></PreferenceRow>
          </div>
        </Section>

        <Section title="会话行为" caption="这些是当前终端执行面的产品边界：默认工作区、环境变量、输出处理和长任务管理。">
          <div className="settings-card terminal-runtime-grid">
            {[
              { icon: FolderOpen, title: "默认工作区", detail: "新命令默认贴近当前项目路径，减少误操作目录。", value: text(runtime.defaultWorkspace, "workspace") },
              { icon: Database, title: "环境变量", detail: "敏感值不在设置页明文展示，运行时按权限注入。", value: "受控" },
              { icon: BrainCircuit, title: "输出摘要", detail: "长 stdout / stderr 保留原始日志，同时给 Agent 提供压缩摘要。", value: "自动" },
              { icon: Activity, title: "长任务", detail: "后台任务需要可追踪 PID，并可在后续面板接入停止/查看日志。", value: "托管" },
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

        <Section title="命令模板" caption="先做安全的一键复制入口，后续可以接入真实模板保存、运行记录和任务队列。">
          <div className="settings-card terminal-template-table">
            <div className="terminal-template-head"><span>名称</span><span>命令</span><span>用途</span><span>操作</span></div>
            {commandTemplates.map((template) => <div className="terminal-template-row" key={template.command}><div className="terminal-name-cell"><Terminal size={14} /><strong>{template.name}</strong></div><code>{template.command}</code><span>{template.detail}</span><div className="terminal-row-actions"><button type="button" onClick={() => void copyText(template.command, `${template.name} command copied.`)}>复制</button></div></div>)}
          </div>
        </Section>

        <Section title="诊断信息" caption="快速确认终端偏好是否已持久化，以及运行时是否正处在可变更状态。">
          <div className="settings-card settings-detail-list">
            <DetailRow label="Settings path" value="terminal.*" detail="通过 settings/set 统一写入桌面设置。" />
            <DetailRow label="Preferred shell" value={prefs.shell} detail="保存为稳定枚举值，展示层再映射成人类可读名称。" />
            <DetailRow label="Runtime state" value={running ? "Turn active" : "Ready for changes"} detail="执行中的 agent turn 不应中途改变关键能力边界。" />
            <DetailRow label="Process policy" value="Permission-aware" detail="真正执行命令时仍由运行时和权限系统决定。" />
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

  const renderPlugins = () => (
    <><div className="settings-page-heading settings-heading-with-action"><div><span className="settings-eyebrow">Extensions</span><h1>Plugins</h1><p>Installed Loom extensions and their current activation state.</p></div><button className="mature-action-button" type="button" onClick={() => { setPlugins(null); setPluginsError(""); }}><RefreshCw size={14} />Refresh</button></div><Section title="Installed plugins" caption="Plugin activation may require a runtime restart."><div className="settings-card plugin-list">{plugins === null && !pluginsError ? <div className="settings-empty-state">Loading plugins…</div> : null}{pluginsError ? <div className="settings-callout-inline"><CircleAlert size={15} /><span>{pluginsError}</span></div> : null}{plugins?.map((plugin, index) => <div className="plugin-row" key={`${plugin.name || "plugin"}-${index}`}><div className="capability-icon"><Box size={17} /></div><div><strong>{plugin.name || "Plugin"}</strong><span>{plugin.description || plugin.source || plugin.version || "Installed extension"}</span></div><StatusPill tone={plugin.enabled === false ? "off" : "ready"}>{plugin.enabled === false ? "Disabled" : plugin.status || "Enabled"}</StatusPill></div>)}{plugins?.length === 0 ? <div className="settings-empty-state">No plugins are installed.</div> : null}</div></Section></>
  );

  const renderMcp = () => {
    const enabled = capabilityEnabled("mcp");
    const badge = capabilityLabel(mcpStatus, enabled);
    return <><div className="settings-page-heading settings-heading-with-switch"><div><span className="settings-eyebrow">Model Context Protocol</span><h1>MCP</h1><p>Inspect MCP discovery and control whether discovered server tools can be exposed.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle MCP" onChange={(value) => void setCapability("mcp", value)} /></div></div><Section title="Discovery"><div className="settings-card settings-detail-list"><DetailRow label="Runtime" value={capabilityStatusText(mcpStatus)} /><DetailRow label="Connected servers" value={String(mcpStatus?.connected_servers ?? "Not reported")} /><DetailRow label="Discovered tools" value={String(mcpStatus?.tool_count ?? "Not reported")} /><DetailRow label="Transport" value={text(mcpStatus?.transport, "Configured server transports")} /></div></Section><div className="settings-callout"><Info size={16} /><div><strong>MCP server editing is the next integration surface.</strong><span>The current runtime already reports discovery health; add/remove/reconnect controls can layer on without changing the capability boundary.</span></div></div></>;
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
    const healthText = errors.length ? `${errors.length} issue(s)` : "Ready / 正常";
    const installedRows: { name: string; description: string; source: string; status: string; tone: string }[] = discoveredSkills.length
      ? discoveredSkills.slice(0, 5).map((skill) => ({
        name: text(skill.name, "unnamed-skill"),
        description: text(skill.short_description || skill.description, "Reusable workflow"),
        source: text(skill.source || skill.scope, "workspace"),
        status: String(skill.scope || "") === "user" ? "已安装" : "已发现",
        tone: String(skill.scope || "") === "user" ? "ready" : "muted",
      }))
      : [
        { name: "release-check", description: "发布前检查与阻塞项整理", source: "user:release-check", status: "示例", tone: "muted" },
        { name: "pdf-helper", description: "PDF 转换、整理与摘要工作流", source: "loom:pdf-helper", status: "示例", tone: "muted" },
        { name: "deploy-check", description: "部署前环境、日志与配置检查", source: "repo:deploy-check", status: "示例", tone: "muted" },
        { name: "docs-polish", description: "文档润色、结构化和发布检查", source: "user:docs-polish", status: "示例", tone: "muted" },
        { name: "browser-debug", description: "浏览器自动化问题定位流程", source: "repo:browser-debug", status: "示例", tone: "muted" },
      ];

    return (
      <>
        <div className="settings-page-heading settings-heading-with-switch skills-page-heading">
          <div>
            <span className="settings-eyebrow">可复用工作流</span>
            <h1>技能</h1>
            <p>集中管理 Codex-compatible SKILL.md 工作流：发现来源、安装入口、运行策略、安全边界和已安装技能。</p>
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

        <Section title="发现状态" caption="分开展示用户偏好、运行时上报和当前工作区可见的技能数量。">
          <div className="skills-stat-grid">
            <div className="skills-stat-card"><span>偏好</span><strong>{enabled ? "开启" : "关闭"}</strong><StatusPill tone={enabled ? "ready" : "off"}>{enabled ? "Active" : "Off"}</StatusPill></div>
            <div className="skills-stat-card"><span>已发现技能</span><strong>{String(discoveredCount)}</strong><small>workspace + user roots</small></div>
            <div className="skills-stat-card"><span>已安装技能</span><strong>{String(installedCount)}</strong><small>&lt;LOOM_HOME&gt;/skills</small></div>
            <div className="skills-stat-card"><span>启用中</span><strong>{String(activeSkillCount)}</strong><small>loaded into context</small></div>
            <div className="skills-stat-card"><span>发现健康度</span><strong>{healthText}</strong><StatusPill tone={errors.length ? "warning" : "ready"}>{errors.length ? "Check" : "Ready"}</StatusPill></div>
          </div>
        </Section>

        <Section title="技能来源" caption="Loom 会从项目目录向上发现 repo skills，再叠加用户级技能根目录。">
          <div className="settings-card skills-source-grid">
            {[
              { icon: FolderOpen, title: "项目来源", path: ".agents/skills", detail: "当前仓库或工作区内的团队技能" },
              { icon: Database, title: "用户来源", path: "~/.agents/skills", detail: "兼容 Codex 风格的本机用户技能" },
              { icon: Sparkles, title: "Loom Home", path: "<LOOM_HOME>/skills", detail: "loom skill install 的默认安装目录" },
            ].map((source) => {
              const Icon = source.icon;
              return <div className="skills-source-card" key={source.path}><span><Icon size={16} /></span><div><strong>{source.title}</strong><code>{source.path}</code><p>{source.detail}</p></div><StatusPill tone="ready">可扫描</StatusPill></div>;
            })}
          </div>
        </Section>

        <Section title="安装与管理" caption="桌面端先给出安全入口和 CLI 快捷命令；真正安装仍走 loom skill 的安全安装器。">
          <div className="settings-card skills-management-card">
            <div className="skills-command-strip">
              <label><Search size={14} /><input placeholder="搜索已安装技能，例如 pdf / browser / deploy" /></label>
              <button type="button" onClick={() => void copyText("loom skill list", "List command copied.")}>列出技能</button>
              <button type="button" onClick={() => void copyText("loom skill update --all", "Update command copied.")}>更新全部</button>
              <button type="button" onClick={() => void copyText("loom skill remove <name>", "Remove command copied.")}>移除命令</button>
            </div>
            <div className="skills-install-grid">
              <button type="button" onClick={() => void copyText("loom skill install ./my-skill", "Local install command copied.")}><FolderOpen size={16} /><strong>安装本地 Skill</strong><span>目录内包含 SKILL.md</span></button>
              <button type="button" onClick={() => void copyText("loom skill install https://github.com/owner/repository/tree/main/path", "GitHub tree command copied.")}><Globe2 size={16} /><strong>从 GitHub 导入</strong><span>仅允许 HTTPS 远程来源</span></button>
              <button type="button" onClick={() => void copyText("loom skill install ./skill.zip", "ZIP command copied.")}><Database size={16} /><strong>从 ZIP 导入</strong><span>自动检查路径穿越</span></button>
              <button type="button" onClick={() => void copyText("loom skill search <query>", "Search command copied.")}><Search size={16} /><strong>搜索技能</strong><span>按名称和描述匹配</span></button>
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
              "仅允许 HTTPS 远程来源",
              "安装时不执行脚本",
              "ZIP 路径穿越检查",
              "Symlink 拒绝 / 跳过",
              "Git 非交互模式",
              "file:// 与 ext:: 传输禁用",
            ].map((item) => <div className="skills-security-row" key={item}><ShieldCheck size={15} /><span>{item}</span><StatusPill tone="ready">已保护</StatusPill></div>)}
          </div>
        </Section>

        <Section title="已安装技能预览" caption="优先展示运行时真实发现结果；无结果时显示示例结构，方便空状态也能看懂页面。">
          <div className="settings-card skills-table">
            <div className="skills-table-head"><span>名称</span><span>简介</span><span>来源</span><span>状态</span><span>操作</span></div>
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
    <><div className="settings-page-heading"><div><span className="settings-eyebrow">Execution safety</span><h1>Permissions</h1><p>Permission profiles define how aggressively Loom can act on the local machine.</p></div></div><Section title="Permission profiles" caption="The active thread can still override its permission mode from the chat surface."><div className="settings-card permission-grid">{(runtime.permissionModes ?? ["approval", "workspace", "full-access"]).map((mode) => <div className={`permission-card ${mode === runtime.defaultPermissionMode ? "selected" : ""}`} key={mode}><ShieldCheck size={18} /><div><strong>{titleCase(mode)}</strong><span>{mode === "full-access" ? "Broad authority for a trusted local environment." : mode === "workspace" ? "Prefer file operations constrained to the active workspace." : "Ask before sensitive or potentially destructive actions."}</span></div>{mode === runtime.defaultPermissionMode ? <StatusPill tone="ready">Default</StatusPill> : null}</div>)}</div></Section><Section title="Safety boundaries"><div className="settings-card settings-detail-list"><DetailRow label="Computer Use" value={capabilityEnabled("computerUse") ? "Available under permission policy" : "Capability off"} /><DetailRow label="Browser" value={capabilityEnabled("browserUse") ? "Available under permission policy" : "Capability off"} /><DetailRow label="Shell / process" value="Permission-aware" /><DetailRow label="Workspace writes" value="Permission-aware" /></div></Section></>
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
    return <><div className="settings-page-heading"><div><span className="settings-eyebrow">Local data</span><h1>Privacy & data</h1><p>Keep diagnostic preferences explicit and make local storage boundaries easy to understand.</p></div></div><Section title="Diagnostics preferences"><div className="settings-card mature-preference-list"><PreferenceRow icon={Activity} title="Usage telemetry" detail="Allow future anonymous product metrics. Off by default."><SettingSwitch checked={prefs.telemetry} label="Usage telemetry" onChange={(value) => void saveSetting("privacy.telemetry", value)} /></PreferenceRow><PreferenceRow icon={CircleAlert} title="Crash reports" detail="Allow future diagnostic crash uploads. Off by default."><SettingSwitch checked={prefs.crashReports} label="Crash reports" onChange={(value) => void saveSetting("privacy.crashReports", value)} /></PreferenceRow></div></Section><Section title="Local storage"><div className="settings-card settings-detail-list"><DetailRow label="Runtime home" value="Loom local runtime directory" detail="Threads, projects, models, and settings remain local unless an integration explicitly sends data elsewhere." /><DetailRow label="Settings store" value="settings.json" detail="Durable capability and desktop preferences." /><DetailRow label="Renderer fallback" value="localStorage" detail="Keeps UI preferences usable if the App Server is temporarily unavailable." /></div></Section><div className="settings-callout"><Info size={16} /><div><strong>No telemetry transport is enabled by these switches today.</strong><span>They are explicit persisted consent preferences for future diagnostics; the current Loom runtime stays local-first.</span></div></div></>;
  };

  const renderDeveloper = () => {
    const diagnostics = JSON.stringify({ runtime, settings, models: modelState }, null, 2);
    return <><div className="settings-page-heading settings-heading-with-action"><div><span className="settings-eyebrow">Diagnostics</span><h1>Developer</h1><p>Runtime details for debugging Loom integrations, settings, and tool exposure.</p></div><button className="mature-action-button" type="button" onClick={() => void copyText(diagnostics, "Diagnostics copied.")}><Copy size={14} />Copy diagnostics</button></div><Section title="Runtime diagnostics"><div className="settings-card settings-detail-list"><DetailRow label="Registered tools" value={String(runtime.registeredToolCount ?? "—")} /><DetailRow label="Exposed tools" value={String(runtime.exposedToolCount ?? "—")} /><DetailRow label="Settings schema" value={String(settings.schemaVersion ?? 1)} /><DetailRow label="Image attachments" value={runtime.attachments?.images === false ? "Disabled" : "Enabled"} /><DetailRow label="File attachments" value={runtime.attachments?.files === false ? "Disabled" : "Enabled"} /></div></Section><Section title="Integration summary"><div className="settings-card settings-detail-list"><DetailRow label="Web Search" value={text(webStatus?.provider, capabilityStatusText(webStatus))} /><DetailRow label="MCP servers" value={String(mcpStatus?.connected_servers ?? "Not reported")} /><DetailRow label="MCP tools" value={String(mcpStatus?.tool_count ?? "Not reported")} /><DetailRow label="Computer Use" value={capabilityStatusText(computerStatus)} /><DetailRow label="Browser Use" value={capabilityStatusText(browserStatus)} /></div></Section><Section title="Raw snapshot" caption="Useful when comparing renderer state with App Server state."><pre className="mature-diagnostics-preview">{diagnostics.slice(0, 6500)}</pre></Section></>;
  };

  const content = (() => {
    if (page === "general") return renderGeneral();
    if (page === "appearance") return renderAppearance();
    if (page === "models") return renderModels();
    if (page === "capabilities") return renderCapabilities();
    if (page === "computer") return renderComputer();
    if (page === "browser") return renderBrowser();
    if (page === "terminal") return renderTerminal();
    if (page === "plugins") return renderPlugins();
    if (page === "mcp") return renderMcp();
    if (page === "skills") return renderSkills();
    if (page === "permissions") return renderPermissions();
    if (page === "shortcuts") return renderShortcuts();
    if (page === "privacy") return renderPrivacy();
    return renderDeveloper();
  })();

  return (
    <div className="settings-shell">
      <aside className="settings-sidebar">
        <div className="settings-sidebar-top">
          <button type="button" className="settings-back" onClick={onClose}><ArrowLeft size={15} /><span>Back to Loom</span></button>
          <div className="settings-sidebar-title"><span className="settings-brand-orb"><Bot size={16} /></span><div><strong>Settings</strong><span>Local agent controls</span></div></div>
          <label className="settings-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search settings" /></label>
        </div>
        <nav className="settings-nav" aria-label="Settings navigation">
          {filteredGroups.map((group) => <section key={group.label}><span className="settings-nav-label">{group.label}</span>{group.items.map((item) => { const Icon = item.icon; return <button type="button" key={item.key} className={page === item.key ? "active" : ""} onClick={() => setPage(item.key)}><Icon size={16} strokeWidth={1.7} /><span>{item.label}</span></button>; })}</section>)}
        </nav>
        <div className="settings-sidebar-footer"><span className="settings-runtime-dot" /><div><strong>Loom runtime</strong><span>{running ? "Turn active" : "Ready for changes"}</span></div></div>
      </aside>
      <main className="settings-main"><div className="settings-main-scroll"><div className="settings-content"><div className="settings-page-surface" key={page}>{content}</div></div></div></main>
      {noticePresence.mounted && visibleNotice ? (
        <div className={`settings-toast ${visibleNotice.tone}`} data-motion-phase={noticePresence.phase}>
          {visibleNotice.tone === "success" ? <Check size={15} /> : <CircleAlert size={15} />}
          <span>{visibleNotice.text}</span>
        </div>
      ) : null}
    </div>
  );
}

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
  ShieldCheck,
  Sparkles,
  Terminal,
  Type,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  DEFAULT_SHORTCUTS,
  SHORTCUTS_CHANGED_EVENT,
  mergeShortcutSettings,
  type ShortcutCommandId,
  type ShortcutSettings,
} from "../keyboardShortcuts";
import type {
  InitializeResult,
  LoomSettings,
  ModelRestartResult,
  ModelSnapshot,
} from "../types/loom";
import { KeyboardShortcutsSettings } from "./KeyboardShortcutsSettings";
import { ModelsSettingsPanel } from "./ModelsSettingsPanel";
import { PermissionsSettings } from "./PermissionsSettings";
import "./settings-page.css";
import "./settings-general-polish.css";
import "./settings-maturity.css";
import "./settings-appearance.css";

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

type BrowserSettings = {
  preferredEngine: "edge" | "chrome" | "system";
  persistSessions: boolean;
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
  preferredEngine: "edge",
  persistSessions: true,
};

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
  { key: "browserUse", title: "Browser Use", description: "Expose browser automation and local CDP attachment.", icon: Globe2, detailPage: "browser" },
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
    return parsed && typeof parsed === "object" ? parsed : {};
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
      // Through unknown: DesktopSettings has no index signature, so TypeScript
      // rightly refuses the direct cast. The dynamic lookup is the point here --
      // the path comes from a settings row at runtime.
      ...((settings as unknown as Record<string, unknown>)[section] as Record<string, unknown> | undefined),
      [key]: value,
    },
  } as DesktopSettings;
}

function applyAppearance(settings: DesktopSettings): void {
  const appearance = { ...DEFAULT_APPEARANCE, ...(settings.appearance ?? {}) } as AppearanceSettings;
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
  const [modelBusy, setModelBusy] = useState("");
  const [busyCapability, setBusyCapability] = useState<CapabilityKey | null>(null);
  const [notice, setNotice] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [plugins, setPlugins] = useState<PluginRecord[] | null>(null);
  const [pluginsError, setPluginsError] = useState("");

  useEffect(() => {
    setSettings((current) => ({ ...mergedSettings(runtime), ...current, capabilities: { ...mergedSettings(runtime).capabilities, ...current.capabilities } }));
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
    const persisted = results.every((result) => result.status === "fulfilled");
    setNotice({
      tone: "success",
      text: persisted ? "Appearance restored to defaults." : "Appearance restored locally; server persistence will catch up later.",
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

  const refreshModels = async () => {
    setModelBusy("refresh");
    try {
      setModelState(await window.loom.listModels<ModelSnapshot>());
      setNotice({ tone: "success", text: "Model profiles refreshed." });
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setModelBusy("");
    }
  };

  const activateModel = async (selection: string) => {
    if (running || modelBusy) return;
    setModelBusy(selection);
    try {
      const result = await window.loom.switchModelProfile<ModelRestartResult>(selection);
      setModelState(result.models);
      setNotice({ tone: "success", text: "Active model updated." });
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setModelBusy("");
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
            <div className="appearance-overview-meta"><span>{widthLabel} conversation</span><span>{appearance.chatFontSize}px chat text</span><span>{appearance.ambientEffects ? "Ambient on" : "Ambient off"}</span></div>
          </div>
          <div className="appearance-workspace-preview" aria-hidden="true">
            <div className="appearance-preview-sidebar" />
            <div className="appearance-preview-main"><span /><span /><span /></div>
            <div className="appearance-preview-inspector" />
          </div>
        </div>

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
            const badge = capabilityLabel(statusFor(item.key), enabled);
            return <div className="capability-row" key={item.key}><div className="capability-icon"><Icon size={17} /></div><div className="capability-copy"><div className="capability-title-line"><strong>{item.title}</strong><StatusPill tone={badge.tone}>{badge.text}</StatusPill></div><span>{item.description}</span></div>{item.detailPage ? <button type="button" className="settings-row-link" onClick={() => setPage(item.detailPage!)}><ChevronRight size={15} /></button> : <span className="settings-row-link-spacer" />}<SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label={`Toggle ${item.title}`} onChange={(value) => void setCapability(item.key, value)} /></div>;
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
    return <><div className="settings-page-heading settings-heading-with-switch"><div><span className="settings-eyebrow">Web interaction</span><h1>Browser</h1><p>Control Loom's browser-use session or attach to a local Chrome/Edge instance.</p></div><div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Browser Use" onChange={(value) => void setCapability("browserUse", value)} /></div></div>
      <Section title="Browser runtime"><div className="settings-card settings-detail-list"><DetailRow label="Backend" value={text(browserStatus?.backend, capabilityStatusText(browserStatus))} /><DetailRow label="Connection" value={text(browserStatus?.browser_connection, "Not reported")} /><DetailRow label="External browser" value={bool(browserStatus?.external_browser) ? "Attached" : "Not reported"} /><DetailRow label="Active sessions" value={String(browserStatus?.active_sessions ?? "Not reported")} /></div></Section>
      <Section title="Browser preferences"><div className="settings-card mature-preference-list"><PreferenceRow icon={Globe2} title="Preferred browser" detail="Preferred desktop browser for compatible Browser Use backends."><SelectControl label="Preferred browser" value={prefs.preferredEngine} options={[{ value: "edge", label: "Microsoft Edge" }, { value: "chrome", label: "Google Chrome" }, { value: "system", label: "System default" }]} onChange={(value) => void saveSetting("browser.preferredEngine", value)} /></PreferenceRow><PreferenceRow icon={Database} title="Persist sessions" detail="Keep compatible browser profiles available across Loom restarts."><SettingSwitch checked={prefs.persistSessions} label="Persist browser sessions" onChange={(value) => void saveSetting("browser.persistSessions", value)} /></PreferenceRow></div></Section>
    </>;
  };

  const renderTerminal = () => {
    const prefs = { ...DEFAULT_TERMINAL, ...(settings.terminal ?? {}) } as TerminalSettings;
    return <><div className="settings-page-heading"><div><span className="settings-eyebrow">Execution environment</span><h1>Terminal</h1><p>Durable preferences for shell-oriented integrations and command execution surfaces.</p></div></div>
      <Section title="Command defaults" caption="Stored centrally so runtime integrations can converge on one desktop preference set."><div className="settings-card mature-preference-list"><PreferenceRow icon={Terminal} title="Preferred shell" detail="Shell preference for integrations that support desktop defaults."><SelectControl label="Preferred shell" value={prefs.shell} options={[{ value: "powershell", label: "PowerShell" }, { value: "cmd", label: "Command Prompt" }, { value: "git-bash", label: "Git Bash" }, { value: "wsl", label: "WSL" }]} onChange={(value) => void saveSetting("terminal.shell", value)} /></PreferenceRow><PreferenceRow icon={Code2} title="Text encoding" detail="Default text encoding preference for terminal surfaces."><SelectControl label="Terminal encoding" value={prefs.encoding} options={[{ value: "utf-8", label: "UTF-8" }, { value: "system", label: "System default" }]} onChange={(value) => void saveSetting("terminal.encoding", value)} /></PreferenceRow><PreferenceRow icon={Gauge} title="Command timeout" detail="Preferred upper bound for foreground command integrations."><SelectControl label="Command timeout" value={String(prefs.commandTimeoutSeconds)} options={[30, 60, 120, 300, 600, 1800].map((value) => ({ value: String(value), label: `${value}s` }))} onChange={(value) => void saveSetting("terminal.commandTimeoutSeconds", Number(value))} /></PreferenceRow><PreferenceRow icon={Activity} title="Background processes" detail="Preserve managed background processes when supported by the runtime."><SettingSwitch checked={prefs.preserveBackgroundProcesses} label="Preserve background processes" onChange={(value) => void saveSetting("terminal.preserveBackgroundProcesses", value)} /></PreferenceRow></div></Section>
      <div className="settings-callout"><Info size={16} /><div><strong>One preference source, multiple runtimes.</strong><span>These values are persisted now; individual exec/browser/computer backends can adopt them without adding new UI controls later.</span></div></div>
    </>;
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
    const errors = Array.isArray(skillsStatus?.errors) ? skillsStatus.errors : [];
    return <><div className="settings-page-heading settings-heading-with-switch"><div><span className="settings-eyebrow">Reusable workflows</span><h1>Skills</h1><p>Codex-compatible SKILL.md workflows discovered from Loom and user skill roots.</p></div><SettingSwitch checked={capabilityEnabled("skills")} disabled={running || busyCapability !== null} label="Toggle Skills" onChange={(value) => void setCapability("skills", value)} /></div><Section title="Discovery"><div className="settings-card settings-detail-list"><DetailRow label="Preference" value={capabilityEnabled("skills") ? "On" : "Off"} /><DetailRow label="Discovered skills" value={String(skillsStatus?.count ?? "Not reported")} /><DetailRow label="Discovery health" value={errors.length ? `${errors.length} issue(s)` : "Ready / not reported"} /></div></Section></>;
  };

  const renderPermissions = () => (
    <PermissionsSettings
      permissionModes={runtime.permissionModes}
      defaultPermissionMode={runtime.defaultPermissionMode}
      running={running}
      computerEnabled={capabilityEnabled("computerUse")}
      browserEnabled={capabilityEnabled("browserUse")}
      onOpenComputer={() => setPage("computer")}
      onOpenBrowser={() => setPage("browser")}
    />
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
      <main className="settings-main"><div className="settings-main-scroll"><div className="settings-content">{content}</div></div></main>
      {notice ? <div className={`settings-toast ${notice.tone}`}>{notice.tone === "success" ? <Check size={15} /> : <CircleAlert size={15} />}<span>{notice.text}</span></div> : null}
    </div>
  );
}

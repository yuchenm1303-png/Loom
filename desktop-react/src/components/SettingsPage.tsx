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
  Gauge,
  Globe2,
  Info,
  Monitor,
  Moon,
  Plug,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Type,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { InitializeResult, ModelSnapshot } from "../types/loom";
import "./settings-page.css";
import "./settings-general-polish.css";

type PageKey =
  | "general"
  | "models"
  | "capabilities"
  | "computer"
  | "browser"
  | "plugins"
  | "skills"
  | "permissions"
  | "developer";

type CapabilityKey =
  | "computerUse"
  | "browserUse"
  | "webSearch"
  | "mcp"
  | "skills"
  | "toolSearch"
  | "codeMode";

type RuntimeView = InitializeResult["runtime"] & {
  settings?: {
    schemaVersion?: number;
    capabilities?: Partial<Record<CapabilityKey, boolean>>;
  };
  capabilityStatus?: Partial<Record<CapabilityKey, Record<string, unknown>>>;
  registeredToolCount?: number;
  exposedToolCount?: number;
  attachments?: {
    images?: boolean;
    files?: boolean;
    maxCount?: number;
  };
};

type PluginRecord = {
  name?: string;
  version?: string;
  enabled?: boolean;
  status?: string;
  source?: string;
  description?: string;
};

type UiScale = "100" | "110" | "120";

type GeneralUiPreferences = {
  scale: UiScale;
  reducedMotion: boolean;
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

const LOCAL_CAPABILITY_STORAGE_KEY = "loom.settings.capabilities";
const GENERAL_UI_STORAGE_KEY = "loom.settings.generalUi";

const DEFAULT_GENERAL_UI: GeneralUiPreferences = {
  scale: "100",
  reducedMotion: false,
};

const DEFAULT_CAPABILITIES: Record<CapabilityKey, boolean> = {
  computerUse: true,
  browserUse: true,
  webSearch: true,
  mcp: true,
  skills: true,
  toolSearch: true,
  codeMode: true,
};

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Loom",
    items: [
      { key: "general", label: "General", icon: Settings2 },
      { key: "models", label: "Models", icon: Cpu },
      { key: "capabilities", label: "Capabilities", icon: Blocks },
    ],
  },
  {
    label: "Integrations",
    items: [
      { key: "computer", label: "Computer Use", icon: Monitor },
      { key: "browser", label: "Browser", icon: Globe2 },
      { key: "plugins", label: "Plugins", icon: Plug },
      { key: "skills", label: "Skills", icon: Sparkles },
    ],
  },
  {
    label: "Advanced",
    items: [
      { key: "permissions", label: "Permissions", icon: ShieldCheck },
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
  {
    key: "computerUse",
    title: "Computer Use",
    description: "Allow Loom to expose desktop control tools for screenshots, UIA, mouse, and keyboard actions.",
    icon: Monitor,
    detailPage: "computer",
  },
  {
    key: "browserUse",
    title: "Browser Use",
    description: "Allow Loom to expose browser automation tools for Chrome/Edge sessions and local CDP attachment.",
    icon: Globe2,
    detailPage: "browser",
  },
  {
    key: "webSearch",
    title: "Web Search",
    description: "Expose the configured public-web search provider to the agent.",
    icon: Search,
  },
  {
    key: "mcp",
    title: "MCP tools",
    description: "Expose tools discovered from configured Model Context Protocol servers.",
    icon: Plug,
  },
  {
    key: "skills",
    title: "Skills",
    description: "Let Loom discover and load reusable SKILL.md workflows for the active workspace.",
    icon: Sparkles,
    detailPage: "skills",
  },
  {
    key: "toolSearch",
    title: "Tool Search",
    description: "Let the agent discover deferred integration tools on demand instead of loading everything up front.",
    icon: Wrench,
  },
  {
    key: "codeMode",
    title: "Code Mode",
    description: "Allow bounded multi-tool composition inside Loom's restricted execution language.",
    icon: Code2,
  },
];

function readStoredCapabilities(): Partial<Record<CapabilityKey, boolean>> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LOCAL_CAPABILITY_STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const result: Partial<Record<CapabilityKey, boolean>> = {};
    for (const key of Object.keys(DEFAULT_CAPABILITIES) as CapabilityKey[]) {
      const value = (parsed as Record<string, unknown>)[key];
      if (typeof value === "boolean") result[key] = value;
    }
    return result;
  } catch {
    return {};
  }
}

function mergedCapabilities(runtime: RuntimeView): Record<CapabilityKey, boolean> {
  return {
    ...DEFAULT_CAPABILITIES,
    ...(runtime.settings?.capabilities ?? {}),
    ...readStoredCapabilities(),
  };
}

function persistLocalCapabilities(capabilities: Record<CapabilityKey, boolean>): void {
  try {
    window.localStorage.setItem(LOCAL_CAPABILITY_STORAGE_KEY, JSON.stringify(capabilities));
    window.dispatchEvent(new CustomEvent("loom:settings-updated", { detail: { capabilities } }));
  } catch {
    // A locked-down renderer still keeps the in-memory switch state for this session.
  }
}

function readGeneralUiPreferences(): GeneralUiPreferences {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(GENERAL_UI_STORAGE_KEY) || "{}");
    const scale = parsed?.scale === "110" || parsed?.scale === "120" ? parsed.scale : "100";
    return {
      scale,
      reducedMotion: parsed?.reducedMotion === true,
    };
  } catch {
    return DEFAULT_GENERAL_UI;
  }
}

function applyGeneralUiPreferences(preferences: GeneralUiPreferences): void {
  const zoom = Number(preferences.scale) / 100;
  document.documentElement.style.setProperty("zoom", String(zoom));
  document.documentElement.dataset.loomReducedMotion = String(preferences.reducedMotion);
}

function persistGeneralUiPreferences(preferences: GeneralUiPreferences): void {
  try {
    window.localStorage.setItem(GENERAL_UI_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // The current renderer can still apply the preference even if persistence is blocked.
  }
  applyGeneralUiPreferences(preferences);
  window.dispatchEvent(new CustomEvent("loom:interface-updated", { detail: preferences }));
}

function bool(value: unknown): boolean {
  return value === true;
}

function text(value: unknown, fallback = "—"): string {
  const resolved = String(value ?? "").trim();
  return resolved || fallback;
}

function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function runtimeAvailable(status?: Record<string, unknown>): boolean | null {
  if (!status) return null;
  if (typeof status.enabled === "boolean") return status.enabled;
  if (typeof status.available === "boolean") return status.available;
  if (status.error) return false;
  return true;
}

function capabilityLabel(status: Record<string, unknown> | undefined, userEnabled: boolean): { text: string; tone: string } {
  if (!userEnabled) return { text: "Preference off", tone: "off" };
  const available = runtimeAvailable(status);
  if (available === null) return { text: "Preference on", tone: "ready" };
  if (!available) return { text: "Preference on · runtime missing", tone: "warning" };
  return { text: "Ready", tone: "ready" };
}

function capabilityStatusText(status: Record<string, unknown> | undefined): string {
  const available = runtimeAvailable(status);
  if (available === null) return "Runtime status not reported";
  if (!available) return "Runtime backend missing";
  return "Runtime backend ready";
}

function SettingSwitch({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange(value: boolean): void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`settings-switch ${checked ? "on" : ""}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
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
      <div className="settings-section-heading">
        <h2>{title}</h2>
        {caption ? <p>{caption}</p> : null}
      </div>
      {children}
    </section>
  );
}

function DetailRow({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="settings-detail-row">
      <div>
        <strong>{label}</strong>
        {detail ? <span>{detail}</span> : null}
      </div>
      <code title={value}>{value}</code>
    </div>
  );
}

function CapabilityCallout({ status, kind }: { status: Record<string, unknown> | undefined; kind: "computer" | "browser" }) {
  const available = runtimeAvailable(status);
  if (available === true) return null;
  if (available === null) {
    return (
      <div className="settings-callout warning">
        <Info size={16} />
        <div>
          <strong>Runtime status is not wired yet.</strong>
          <span>The switch is enabled, but the App Server has not reported whether the {kind === "computer" ? "Windows operator" : "browser backend"} is actually ready.</span>
        </div>
      </div>
    );
  }
  return (
    <div className="settings-callout warning">
      <CircleAlert size={16} />
      <div>
        <strong>{kind === "computer" ? "Computer Use" : "Browser Use"} runtime is missing.</strong>
        <span>The preference switch is on, but Loom still needs the backend dependency before this capability can run.</span>
      </div>
    </div>
  );
}

export function SettingsPage({ runtime, models, running, onClose }: SettingsPageProps) {
  const [page, setPage] = useState<PageKey>("general");
  const [query, setQuery] = useState("");
  const [busyCapability, setBusyCapability] = useState<CapabilityKey | null>(null);
  const [localCapabilities, setLocalCapabilities] = useState<Record<CapabilityKey, boolean>>(() => mergedCapabilities(runtime));
  const [generalUi, setGeneralUi] = useState<GeneralUiPreferences>(() => readGeneralUiPreferences());
  const [notice, setNotice] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [plugins, setPlugins] = useState<PluginRecord[] | null>(null);
  const [pluginsError, setPluginsError] = useState("");

  useEffect(() => {
    setLocalCapabilities(mergedCapabilities(runtime));
  }, [runtime.settings]);

  useEffect(() => {
    applyGeneralUiPreferences(generalUi);
  }, [generalUi]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (page !== "plugins" || plugins !== null || pluginsError) return;
    let disposed = false;
    void window.loom.call<{ plugins?: PluginRecord[] }>("plugin/list", {})
      .then((result) => {
        if (!disposed) setPlugins(Array.isArray(result.plugins) ? result.plugins : []);
      })
      .catch((cause) => {
        if (!disposed) setPluginsError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => { disposed = true; };
  }, [page, plugins, pluginsError]);

  const filteredGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return NAV_GROUPS;
    return NAV_GROUPS
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => item.label.toLowerCase().includes(needle)),
      }))
      .filter((group) => group.items.length);
  }, [query]);

  const statusFor = (key: CapabilityKey) => runtime.capabilityStatus?.[key];
  const computerStatus = statusFor("computerUse");
  const browserStatus = statusFor("browserUse");
  const skillsStatus = statusFor("skills");
  const mcpStatus = statusFor("mcp");
  const webStatus = statusFor("webSearch");
  const currentModel = models?.current;

  const setCapability = async (key: CapabilityKey, enabled: boolean) => {
    if (running || busyCapability) return;
    setBusyCapability(key);
    const nextCapabilities = { ...localCapabilities, [key]: enabled };
    setLocalCapabilities(nextCapabilities);
    persistLocalCapabilities(nextCapabilities);
    try {
      await window.loom.call("settings/set", { capability: key, enabled });
      setNotice({ tone: "success", text: `${CAPABILITIES.find((item) => item.key === key)?.title || key} preference saved.` });
    } catch {
      setNotice({ tone: "success", text: `${CAPABILITIES.find((item) => item.key === key)?.title || key} preference saved locally.` });
    } finally {
      setBusyCapability(null);
    }
  };

  const updateGeneralUi = (patch: Partial<GeneralUiPreferences>) => {
    const next = { ...generalUi, ...patch };
    setGeneralUi(next);
    persistGeneralUiPreferences(next);
  };

  const resetGeneralUi = () => {
    setGeneralUi(DEFAULT_GENERAL_UI);
    persistGeneralUiPreferences(DEFAULT_GENERAL_UI);
    setNotice({ tone: "success", text: "Interface preferences reset." });
  };

  const copyWorkspace = async () => {
    const value = text(runtime.defaultWorkspace, "");
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setNotice({ tone: "success", text: "Workspace path copied." });
    } catch {
      setNotice({ tone: "error", text: "Could not copy the workspace path." });
    }
  };

  const renderCapabilities = () => (
    <>
      <div className="settings-page-heading">
        <div>
          <span className="settings-eyebrow">Agent runtime</span>
          <h1>Capabilities</h1>
          <p>Choose which major tool families Loom should expose. Preference switches and backend readiness are shown separately so disabled dependencies are not confused with a closed switch.</p>
        </div>
        <span className="settings-tool-count">{runtime.exposedToolCount ?? "—"} / {runtime.registeredToolCount ?? "—"} tools exposed</span>
      </div>

      {running ? (
        <div className="settings-callout warning">
          <CircleAlert size={16} />
          <div><strong>Finish or stop the active turn first.</strong><span>Capability switches are locked while an agent turn is running so the tool set cannot change mid-execution.</span></div>
        </div>
      ) : null}

      <Section title="Agent capabilities" caption="The switch is the user's preference. The status badge reports whether the runtime is known to be ready.">
        <div className="settings-card capability-list">
          {CAPABILITIES.map((item) => {
            const Icon = item.icon;
            const enabled = localCapabilities[item.key];
            const badge = capabilityLabel(statusFor(item.key), enabled);
            return (
              <div className="capability-row" key={item.key}>
                <div className="capability-icon"><Icon size={17} strokeWidth={1.75} /></div>
                <div className="capability-copy">
                  <div className="capability-title-line">
                    <strong>{item.title}</strong>
                    <StatusPill tone={badge.tone}>{badge.text}</StatusPill>
                  </div>
                  <span>{item.description}</span>
                </div>
                {item.detailPage ? (
                  <button type="button" className="settings-row-link" onClick={() => setPage(item.detailPage!)} aria-label={`Open ${item.title} settings`}>
                    <ChevronRight size={15} />
                  </button>
                ) : <span className="settings-row-link-spacer" />}
                <SettingSwitch
                  checked={enabled}
                  disabled={running || busyCapability !== null}
                  label={`Toggle ${item.title}`}
                  onChange={(value) => void setCapability(item.key, value)}
                />
              </div>
            );
          })}
        </div>
      </Section>
    </>
  );

  const renderGeneral = () => {
    const modelLabel = currentModel?.name || currentModel?.model || text(runtime.model);
    const permissionLabel = titleCase(text(runtime.defaultPermissionMode, "approval"));
    const enabledCapabilities = Object.values(localCapabilities).filter(Boolean).length;
    const toolLabel = `${runtime.exposedToolCount ?? "—"} / ${runtime.registeredToolCount ?? "—"}`;
    const attachmentLabel = runtime.attachments?.images === false && runtime.attachments?.files === false
      ? "Off"
      : `${runtime.attachments?.maxCount ?? "—"} max`;

    return (
      <>
        <div className="settings-page-heading general-page-heading">
          <div>
            <span className="settings-eyebrow">Loom desktop</span>
            <h1>General</h1>
            <p>Manage the defaults and presentation of your local agent workspace from one place.</p>
          </div>
          <div className={`general-heading-status ${running ? "running" : ""}`}>
            <i />
            <span>{running ? "Agent turn active" : "Runtime ready"}</span>
          </div>
        </div>

        <div className="general-overview-card">
          <div className="general-overview-copy">
            <div className="general-overview-icon"><Activity size={19} strokeWidth={1.8} /></div>
            <strong>{running ? "Loom is working" : "Your local runtime is ready"}</strong>
            <p>One view of the model, permission boundary, exposed tools, and interface preferences currently shaping new agent work.</p>
          </div>
          <div className="general-stat-grid">
            <div className="general-stat"><Cpu size={17} /><div><span>Model</span><strong title={modelLabel}>{modelLabel}</strong></div></div>
            <div className="general-stat"><ShieldCheck size={17} /><div><span>Permission</span><strong>{permissionLabel}</strong></div></div>
            <div className="general-stat"><Wrench size={17} /><div><span>Tools exposed</span><strong>{toolLabel}</strong></div></div>
            <div className="general-stat"><Blocks size={17} /><div><span>Capabilities</span><strong>{enabledCapabilities} / {CAPABILITIES.length} enabled · {attachmentLabel}</strong></div></div>
          </div>
        </div>

        <Section title="Runtime defaults" caption="The values Loom starts with when you create a new conversation.">
          <div className="general-default-grid">
            <div className="general-default-card">
              <div className="general-default-card-head"><span className="general-default-card-icon"><Gauge size={16} /></span></div>
              <label>Default workspace</label>
              <strong title={text(runtime.defaultWorkspace)}>{text(runtime.defaultWorkspace)}</strong>
              <p>New conversations begin here when no project is selected.</p>
              <div className="general-workspace-actions">
                <button type="button" className="general-copy-button" onClick={() => void copyWorkspace()}><Copy size={13} />Copy path</button>
              </div>
            </div>

            <button type="button" className="general-default-card" onClick={() => setPage("permissions")}>
              <div className="general-default-card-head"><span className="general-default-card-icon"><ShieldCheck size={16} /></span><ChevronRight size={15} /></div>
              <label>Default permission</label>
              <strong>{permissionLabel}</strong>
              <p>Review the execution boundary used for sensitive actions.</p>
            </button>

            <button type="button" className="general-default-card" onClick={() => setPage("models")}>
              <div className="general-default-card-head"><span className="general-default-card-icon"><Cpu size={16} /></span><ChevronRight size={15} /></div>
              <label>Current model</label>
              <strong title={modelLabel}>{modelLabel}</strong>
              <p>Open model profiles and inspect the active inference route.</p>
            </button>
          </div>
        </Section>

        <Section title="Interface" caption="Tune Loom for your screen without changing agent behavior.">
          <div className="settings-card general-interface-card">
            <div className="general-theme-row">
              <div className="general-theme-card selected">
                <div className="general-theme-swatch" />
                <div><strong>Loom Dark</strong><span>Current desktop theme</span></div>
                <Check size={16} />
              </div>
              <div className="general-theme-card">
                <div className="general-theme-swatch system" />
                <div><strong>System theme</strong><span>Follow the operating system</span></div>
                <span className="general-soon-badge">Soon</span>
              </div>
            </div>

            <div className="general-preference-list">
              <div className="general-preference-row">
                <Type size={17} />
                <div className="general-preference-copy"><strong>Interface scale</strong><span>Scale the entire desktop surface for more comfortable reading.</span></div>
                <div className="general-scale-control" role="group" aria-label="Interface scale">
                  {(["100", "110", "120"] as UiScale[]).map((scale) => (
                    <button type="button" key={scale} className={generalUi.scale === scale ? "active" : ""} onClick={() => updateGeneralUi({ scale })}>{scale}%</button>
                  ))}
                </div>
              </div>

              <div className="general-preference-row">
                <Moon size={17} />
                <div className="general-preference-copy"><strong>Reduce motion</strong><span>Minimize decorative transitions, pulses, and animated status effects.</span></div>
                <SettingSwitch checked={generalUi.reducedMotion} label="Reduce interface motion" onChange={(value) => updateGeneralUi({ reducedMotion: value })} />
              </div>

              <div className="general-preference-row">
                <RotateCcw size={17} />
                <div className="general-preference-copy"><strong>Reset presentation</strong><span>Return scale and motion preferences to the Loom defaults.</span></div>
                <button type="button" className="general-reset-button" onClick={resetGeneralUi}><RotateCcw size={13} />Reset</button>
              </div>
            </div>
          </div>
        </Section>

        <Section title="Quick access" caption="Jump straight to the settings that most often affect an agent run.">
          <div className="general-quick-grid">
            <button type="button" className="general-quick-card" onClick={() => setPage("capabilities")}><Blocks size={18} /><div><strong>Capabilities</strong><span>Choose the tool families Loom can expose</span></div><ChevronRight size={15} /></button>
            <button type="button" className="general-quick-card" onClick={() => setPage("models")}><BrainCircuit size={18} /><div><strong>Models</strong><span>Inspect active inference and saved profiles</span></div><ChevronRight size={15} /></button>
            <button type="button" className="general-quick-card" onClick={() => setPage("developer")}><Wrench size={18} /><div><strong>Runtime diagnostics</strong><span>Inspect tools, attachments, and integrations</span></div><ChevronRight size={15} /></button>
          </div>
        </Section>
      </>
    );
  };

  const renderModels = () => (
    <>
      <div className="settings-page-heading"><div><span className="settings-eyebrow">Inference</span><h1>Models</h1><p>Inspect the active model and saved profiles. Fast per-task switching can stay in the composer; full configuration belongs here.</p></div></div>
      <Section title="Active model">
        <div className="settings-card model-summary-card">
          <div className="model-summary-icon"><BrainCircuit size={22} /></div>
          <div className="model-summary-copy">
            <span className="settings-eyebrow">Current</span>
            <strong>{currentModel?.name || currentModel?.model || text(runtime.model)}</strong>
            <span>{currentModel ? `${titleCase(currentModel.provider || currentModel.adapter)} · ${currentModel.model}` : "Runtime model"}</span>
          </div>
          <StatusPill tone="ready">Ready</StatusPill>
        </div>
      </Section>
      <Section title="Saved profiles" caption="Profiles configured in Loom's model manager.">
        <div className="settings-card settings-detail-list">
          {(models?.profiles ?? []).map((profile) => (
            <DetailRow key={profile.selection} label={profile.name} value={profile.model} detail={`${titleCase(profile.adapter)} · ${profile.baseUrl || "default endpoint"}`} />
          ))}
          {!models?.profiles?.length ? <div className="settings-empty-state">No saved model profiles.</div> : null}
        </div>
      </Section>
    </>
  );

  const renderComputer = () => {
    const enabled = localCapabilities.computerUse;
    const badge = capabilityLabel(computerStatus, enabled);
    return (
      <>
        <div className="settings-page-heading settings-heading-with-switch">
          <div><span className="settings-eyebrow">Desktop integration</span><h1>Computer Use</h1><p>Screenshot-driven Windows control with UI Automation assistance and post-action verification.</p></div>
          <div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Computer Use" onChange={(value) => void setCapability("computerUse", value)} /></div>
        </div>
        {enabled ? <CapabilityCallout status={computerStatus} kind="computer" /> : null}
        <Section title="Runtime status">
          <div className="settings-card settings-detail-list">
            <DetailRow label="Preference" value={enabled ? "On" : "Off"} detail="Controls whether Loom should expose Computer Use tools." />
            <DetailRow label="Backend status" value={capabilityStatusText(computerStatus)} detail="This must be ready before Loom can actually click, type, or observe desktop apps." />
            <DetailRow label="Operator" value={text(computerStatus?.operator, "Not reported")} detail="Backend responsible for screenshot capture, UIA discovery, and physical input." />
            <DetailRow label="Grounder" value={text(computerStatus?.grounder, "Not reported")} detail="Visual policy used for screenshot-based target selection." />
            <DetailRow label="Policy step" value={bool(computerStatus?.policy_step_enabled) ? "Enabled" : "Not reported"} detail="Whether computer_step can plan and execute one grounded GUI action." />
            <DetailRow label="Observation" value={text(computerStatus?.observation_mode, "Not reported")} />
            <DetailRow label="Verification" value={text(computerStatus?.verification, "Not reported")} />
          </div>
        </Section>
        <Section title="Safety boundary" caption="Computer Use remains subject to the conversation's permission profile even when the master switch is on.">
          <div className="settings-card safety-summary">
            <ShieldCheck size={18} />
            <div><strong>Sensitive GUI actions still cross Loom permissions.</strong><span>Turning Computer Use on only allows exposure; it does not bypass Approval, Workspace, or Full Access policy.</span></div>
          </div>
        </Section>
      </>
    );
  };

  const renderBrowser = () => {
    const enabled = localCapabilities.browserUse;
    const badge = capabilityLabel(browserStatus, enabled);
    return (
      <>
        <div className="settings-page-heading settings-heading-with-switch">
          <div><span className="settings-eyebrow">Web interaction</span><h1>Browser</h1><p>Control Loom's browser-use session or attach to a local Chrome/Edge instance through loopback-only CDP.</p></div>
          <div className="settings-master-switch"><StatusPill tone={badge.tone}>{badge.text}</StatusPill><SettingSwitch checked={enabled} disabled={running || busyCapability !== null} label="Toggle Browser Use" onChange={(value) => void setCapability("browserUse", value)} /></div>
        </div>
        {enabled ? <CapabilityCallout status={browserStatus} kind="browser" /> : null}
        <Section title="Browser runtime">
          <div className="settings-card settings-detail-list">
            <DetailRow label="Preference" value={enabled ? "On" : "Off"} detail="Controls whether Loom should expose Browser Use tools." />
            <DetailRow label="Backend status" value={capabilityStatusText(browserStatus)} detail="This must be ready before Loom can open, attach, or operate browser pages." />
            <DetailRow label="Backend" value={text(browserStatus?.backend, "Not reported")} />
            <DetailRow label="Connection" value={text(browserStatus?.browser_connection, "Not reported")} detail="local-launch starts Loom's browser; cdp-attach controls an existing local Chrome/Edge process." />
            <DetailRow label="External browser" value={bool(browserStatus?.external_browser) ? "Attached" : "Not reported"} />
            <DetailRow label="Session persistence" value={text(browserStatus?.session_persistence, "Not reported")} />
            <DetailRow label="Active sessions" value={String(browserStatus?.active_sessions ?? "Not reported")} />
          </div>
        </Section>
      </>
    );
  };

  const renderPlugins = () => (
    <>
      <div className="settings-page-heading"><div><span className="settings-eyebrow">Extensions</span><h1>Plugins</h1><p>Installed Loom extensions belong here, separate from built-in runtime capabilities.</p></div></div>
      <Section title="Installed plugins" caption="Plugin activation may require a runtime restart.">
        <div className="settings-card plugin-list">
          {plugins === null && !pluginsError ? <div className="settings-empty-state">Loading plugins…</div> : null}
          {pluginsError ? <div className="settings-callout-inline"><CircleAlert size={15} /><span>{pluginsError}</span></div> : null}
          {plugins?.map((plugin, index) => (
            <div className="plugin-row" key={`${plugin.name || "plugin"}-${index}`}>
              <div className="capability-icon"><Box size={17} /></div>
              <div><strong>{plugin.name || "Plugin"}</strong><span>{plugin.description || plugin.source || plugin.version || "Installed extension"}</span></div>
              <StatusPill tone={plugin.enabled === false ? "off" : "ready"}>{plugin.enabled === false ? "Disabled" : plugin.status || "Enabled"}</StatusPill>
            </div>
          ))}
          {plugins?.length === 0 ? <div className="settings-empty-state">No plugins are installed.</div> : null}
        </div>
      </Section>
    </>
  );

  const renderSkills = () => {
    const skillErrors = Array.isArray(skillsStatus?.errors) ? skillsStatus.errors : [];
    return (
      <>
        <div className="settings-page-heading settings-heading-with-switch">
          <div><span className="settings-eyebrow">Reusable workflows</span><h1>Skills</h1><p>Codex-compatible SKILL.md workflows discovered from Loom and user skill roots.</p></div>
          <SettingSwitch checked={localCapabilities.skills} disabled={running || busyCapability !== null} label="Toggle Skills" onChange={(value) => void setCapability("skills", value)} />
        </div>
        <Section title="Discovery">
          <div className="settings-card settings-detail-list">
            <DetailRow label="Preference" value={localCapabilities.skills ? "On" : "Off"} />
            <DetailRow label="Discovered skills" value={String(skillsStatus?.count ?? "Not reported")} />
            <DetailRow label="Discovery health" value={skillErrors.length ? `${skillErrors.length} issue(s)` : "Not reported"} />
          </div>
        </Section>
      </>
    );
  };

  const renderPermissions = () => (
    <>
      <div className="settings-page-heading"><div><span className="settings-eyebrow">Execution safety</span><h1>Permissions</h1><p>Permission profiles determine whether sensitive file, process, browser, and GUI actions run automatically or require approval.</p></div></div>
      <Section title="Permission profiles">
        <div className="settings-card permission-grid">
          {(runtime.permissionModes ?? ["approval", "workspace", "full-access"]).map((mode) => (
            <div className={`permission-card ${mode === runtime.defaultPermissionMode ? "selected" : ""}`} key={mode}>
              <ShieldCheck size={18} />
              <div><strong>{titleCase(mode)}</strong><span>{mode === "full-access" ? "Broad execution authority for trusted local work." : mode === "workspace" ? "Prefer actions constrained to the active workspace." : "Ask before sensitive actions."}</span></div>
              {mode === runtime.defaultPermissionMode ? <StatusPill tone="ready">Default</StatusPill> : null}
            </div>
          ))}
        </div>
      </Section>
    </>
  );

  const renderDeveloper = () => (
    <>
      <div className="settings-page-heading"><div><span className="settings-eyebrow">Diagnostics</span><h1>Developer</h1><p>Runtime details useful when debugging Loom integrations and tool exposure.</p></div></div>
      <Section title="Runtime diagnostics">
        <div className="settings-card settings-detail-list">
          <DetailRow label="Registered tools" value={String(runtime.registeredToolCount ?? "—")} />
          <DetailRow label="Exposed tools" value={String(runtime.exposedToolCount ?? "—")} />
          <DetailRow label="Image attachments" value={runtime.attachments?.images === false ? "Disabled" : "Enabled"} />
          <DetailRow label="File attachments" value={runtime.attachments?.files === false ? "Disabled" : "Enabled"} />
          <DetailRow label="Capability schema" value={String(runtime.settings?.schemaVersion ?? 1)} />
        </div>
      </Section>
      <Section title="Integration summary">
        <div className="settings-card settings-detail-list">
          <DetailRow label="Web Search" value={text(webStatus?.provider, capabilityStatusText(webStatus))} />
          <DetailRow label="MCP servers" value={String(mcpStatus?.connected_servers ?? "Not reported")} />
          <DetailRow label="MCP tools" value={String(mcpStatus?.tool_count ?? "Not reported")} />
          <DetailRow label="Computer Use" value={capabilityStatusText(computerStatus)} />
          <DetailRow label="Browser Use" value={capabilityStatusText(browserStatus)} />
        </div>
      </Section>
    </>
  );

  const content = (() => {
    if (page === "general") return renderGeneral();
    if (page === "models") return renderModels();
    if (page === "capabilities") return renderCapabilities();
    if (page === "computer") return renderComputer();
    if (page === "browser") return renderBrowser();
    if (page === "plugins") return renderPlugins();
    if (page === "skills") return renderSkills();
    if (page === "permissions") return renderPermissions();
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
          {filteredGroups.map((group) => (
            <section key={group.label}>
              <span className="settings-nav-label">{group.label}</span>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button type="button" key={item.key} className={page === item.key ? "active" : ""} onClick={() => setPage(item.key)}>
                    <Icon size={16} strokeWidth={1.7} />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </section>
          ))}
        </nav>

        <div className="settings-sidebar-footer">
          <span className="settings-runtime-dot" />
          <div><strong>Loom runtime</strong><span>{running ? "Turn active" : "Ready for changes"}</span></div>
        </div>
      </aside>

      <main className="settings-main">
        <div className="settings-main-scroll">
          <div className="settings-content">{content}</div>
        </div>
      </main>

      {notice ? <div className={`settings-toast ${notice.tone}`}>{notice.tone === "success" ? <Check size={15} /> : <CircleAlert size={15} />}<span>{notice.text}</span></div> : null}
    </div>
  );
}
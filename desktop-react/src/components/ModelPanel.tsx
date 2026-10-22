import {
  BrainCircuit,
  Cat,
  Check,
  ChevronDown,
  ChevronRight,
  KeyRound,
  LoaderCircle,
  Lock,
  Music2,
  Plus,
  Search,
  Shapes,
  SlidersHorizontal,
  Trash2,
  X,
} from "./icons";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import type {
  AddModelInput,
  ModelProfile,
  ModelReasoningOption,
  ModelReasoningState,
  ModelSnapshot,
} from "../types/loom";
import minimaxLogo from "../assets/provider-logos/minimax.svg";
import deepseekLogo from "../assets/provider-logos/deepseek.svg";
import opencodeLogo from "../assets/provider-logos/opencode.svg";
import openaiLogo from "../assets/provider-logos/openai.svg";
import qwenLogo from "../assets/provider-logos/qwen.svg";
import moonshotLogo from "../assets/provider-logos/moonshotai.svg";
import xiaomiLogo from "../assets/provider-logos/xiaomi.svg";
import hunyuanLogo from "../assets/provider-logos/tencenthy.svg";
import xLogo from "../assets/provider-logos/x.svg";
import { useI18n } from "../i18n";
import { PanelFoot, PanelHead, PanelLabel } from "./composerPanel";
import { ReasoningControl } from "./ReasoningControl";
import { personalConnectionTemplate } from "../state/personalModelConnection";

/*
 * The model card: what answers this conversation and how hard it thinks (home), every model by provider (the library),
 * one provider's models, and two forms (a personal connection, a custom model ID). It is the content of the composer's
 * model panel; the shell, the motion and every rule it wears belong to the composer panel family (composerPanel.tsx,
 * composer-panels.css). Each view is its own element, so a view that is swapped in fades in like the panel did.
 */

type ModelView = "list" | "profiles" | "group" | "add" | "custom";

interface ModelGroup {
  id: string;
  name: string;
  order: number;
  profiles: ModelProfile[];
}

interface ProviderCredentialTarget {
  provider: string;
  label: string;
  placeholder: string;
}

function providerCredentialTarget(groupId: string): ProviderCredentialTarget | null {
  if (groupId === "opencode-go") {
    return {
      provider: "opencode-go",
      label: "OpenCode Go",
      placeholder: "OpenCode Go subscription key",
    };
  }
  if (groupId === "managed-relay" || groupId.startsWith("managed-relay:")) {
    return {
      provider: "managed-relay",
      label: "Muxway Relay",
      placeholder: "Muxway group API key",
    };
  }
  return null;
}

// Every saved connection arrives as its own `saved:<id>` group that shares the
// display name "Custom APIs". Present them as one provider instead of a stack
// of identically named rows.
const SAVED_GROUP_ID = "saved";

function profileGroupId(profile: ModelProfile): string {
  if (profile.kind === "saved") return SAVED_GROUP_ID;
  return profile.groupId || profile.selection;
}

function visibleProfiles(group: ModelGroup): ModelProfile[] {
  return group.profiles.filter((profile) => !profile.setupOnly);
}

function selectableProfiles(group: ModelGroup): ModelProfile[] {
  return visibleProfiles(group).filter((profile) => profile.available !== false);
}

interface ProviderSetup {
  credentialTarget: ProviderCredentialTarget | null;
  statusProfiles: ModelProfile[];
  catalogUnauthorized: boolean;
  needsKey: boolean;
}

function providerSetup(group: ModelGroup): ProviderSetup {
  const credentialTarget = providerCredentialTarget(group.id);
  const statusProfiles = group.profiles.filter((profile) => profile.setupOnly && profile.statusMessage);
  const catalogUnauthorized = statusProfiles.some((profile) => profile.statusMessage?.includes("HTTP 401"));
  const needsKey = Boolean(
    credentialTarget
    && (group.profiles.every((profile) => profile.configured === false) || catalogUnauthorized),
  );
  return { credentialTarget, statusProfiles, catalogUnauthorized, needsKey };
}

// Hue and chroma feed OKLCH in composer-panels.css (.cp-mark) for both logos and fallback monograms.
const PROVIDER_LOGOS: Record<string, string> = {
  minimax: minimaxLogo,
  "managed-relay:minimax": minimaxLogo,
  deepseek: deepseekLogo,
  "opencode-go": opencodeLogo,
  "opencode-console": opencodeLogo,
  "managed-relay:openai": openaiLogo,
  gpt: openaiLogo,
  kimi: moonshotLogo,
  qwen: qwenLogo,
  mimo: xiaomiLogo,
  hunyuan: hunyuanLogo,
  grok: xLogo,
};
const PROVIDER_TINTS: Record<string, readonly [hue: number, chroma: number]> = {
  minimax: [16, 0.14],
  "managed-relay:minimax": [16, 0.14],
  deepseek: [262, 0.13],
  "opencode-go": [168, 0.1],
  "opencode-console": [168, 0.1],
  "managed-relay": [295, 0.13],
  "managed-relay:openai": [0, 0],
  [SAVED_GROUP_ID]: [72, 0.11],
};
const FALLBACK_HUES = [28, 145, 205, 238, 330];

function providerTint(id: string): readonly [number, number] {
  const known = PROVIDER_TINTS[id];
  if (known) return known;
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return [FALLBACK_HUES[hash % FALLBACK_HUES.length], 0.11];
}

type MarkSize = "small" | "default" | "large";

function markClass(size: MarkSize): string {
  return size === "small" ? "cp-mark is-small" : size === "large" ? "cp-mark is-large" : "cp-mark";
}

function ProviderMark({ id, name, size = "default" }: { id: string; name: string; size?: MarkSize }) {
  const [hue, chroma] = providerTint(id);
  const logo = PROVIDER_LOGOS[id] ?? (name === "OpenAI" ? openaiLogo : undefined);
  const initial = name.trim().match(/[\p{L}\p{N}]/u)?.[0]?.toUpperCase() ?? "?";
  return (
    <span className={markClass(size)} style={{ "--cp-hue": hue, "--cp-chroma": chroma } as CSSProperties} aria-hidden="true">
      {logo ? <span className="cp-mark-logo" style={{ "--cp-logo": `url("${logo}")` } as CSSProperties} /> : initial}
    </span>
  );
}

function FamilyMark({ family }: { family: string }) {
  const icons: Record<string, ReactNode> = {
    GLM: <BrainCircuit size={12} strokeWidth={1.75} />,
    LongCat: <Cat size={12} strokeWidth={1.75} />,
    Muse: <Music2 size={12} strokeWidth={1.75} />,
    Other: <Shapes size={12} strokeWidth={1.75} />,
  };
  const icon = icons[family];
  if (!icon) return <ProviderMark id={family.toLowerCase()} name={family} size="small" />;
  const [hue, chroma] = providerTint(family.toLowerCase());
  return (
    <span className="cp-mark is-small" style={{ "--cp-hue": hue, "--cp-chroma": chroma } as CSSProperties} aria-hidden="true">
      {icon}
    </span>
  );
}

function queryTokens(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

// Every token has to match. Tokens may name the provider ("opencode kimi"),
// but at least one must hit the model itself, so "open" lists providers
// instead of every model they serve.
function profileMatches(profile: ModelProfile, providerName: string, tokens: string[]): boolean {
  const own = `${profile.name} ${profile.model} ${profile.family ?? ""}`.toLowerCase();
  const provider = providerName.toLowerCase();
  let ownMatch = false;
  for (const token of tokens) {
    if (own.includes(token)) ownMatch = true;
    else if (!provider.includes(token)) return false;
  }
  return ownMatch;
}

function compactId(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

// Most built-in names are a prettified model ID ("DeepSeek V4 Pro" for
// deepseek-v4-pro). Only print the ID when it tells the user something new.
function modelIdAddsInfo(profile: ModelProfile): boolean {
  return Boolean(profile.model) && compactId(profile.name) !== compactId(profile.model);
}

// Only the official Console zero-priced OpenAI-chat IDs receive a Free label.
const CONSOLE_FREE_MODELS = new Set([
  "big-pickle", "glm-5.3-flash-free", "deepseek-v4.1-flash-free",
  "space-bunny-free", "cat-jam-free", "ll-alpha-free",
  "longcat-2.5-preview-free", "exo-free", "mimo-v2.6-flash-free",
  "mimo-v2.5-free", "nemotron-3-ultra-free",
  "nemotron-3.5-lightning-free", "nemotron-3.5-super-vl-free",
]);
function isConsoleFree(profile: ModelProfile): boolean {
  return profile.groupId === "opencode-console" && CONSOLE_FREE_MODELS.has(profile.model.toLowerCase());
}
const CONSOLE_FREE_RESTRICTION = "OpenCode currently restricts free-tier models to its own app; third-party clients such as Loom receive HTTP 403. This model cannot be used from Loom.";

function modelPriceLabel(profile: ModelProfile): string | null {
  if (isConsoleFree(profile)) return "Free · OpenCode only";
  if (profile.groupId === "opencode-console") return "Pay as you go";
  return null;
}

function profileTooltip(profile: ModelProfile): string {
  return [profile.name, profile.model === profile.name ? "" : profile.model, profile.protocol || ""]
    .filter(Boolean)
    .join(" · ");
}

function unavailableLabel(profile: ModelProfile): string {
  const message = String(profile.statusMessage || "");
  if (message.includes("Sign in to Loom")) return "Sign in";
  if (message.includes("Loom Admin")) return "Admin blocked";
  if (message.includes("Model metadata unavailable")) return "Host update required";
  if (profile.catalogState === "retired") return "Retired";
  if (profile.accessState === "not-listed") return "Not listed";
  return "Unavailable";
}

function unavailableReason(profile: ModelProfile): string {
  if (profile.catalogState === "retired") return "This model is no longer offered by the provider. It remains listed for older conversations; choose an available model to continue.";
  if (profile.accessState === "not-listed") return "This model has not been confirmed in the provider's current directory.";
  return profile.statusMessage || "This model is currently unavailable.";
}

const ITEM = "[data-cp-item]:not(:disabled)";

function PanelSearch({
  value,
  placeholder,
  label,
  onChange,
}: {
  value: string;
  placeholder: string;
  label: string;
  onChange(value: string): void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  return (
    <label className="cp-search">
      <Search size={15} strokeWidth={1.75} aria-hidden="true" />
      <input
        ref={inputRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            // First Escape clears the query; the next one closes the panel.
            event.preventDefault();
            event.stopPropagation();
            onChange("");
          } else if (event.key === "Enter" && value.trim()) {
            event.preventDefault();
            event.currentTarget.closest(".cp-view")?.querySelector<HTMLElement>(ITEM)?.click();
          }
        }}
        placeholder={placeholder}
        aria-label={label}
        autoFocus
        autoComplete="off"
        spellCheck={false}
      />
      {value ? (
        <button
          type="button"
          className="cp-icon"
          aria-label="Clear search"
          onClick={() => {
            onChange("");
            inputRef.current?.focus();
          }}
        >
          <X size={13} strokeWidth={2} />
        </button>
      ) : null}
    </label>
  );
}

function ProviderRow({
  group, current, accountAuthenticated, zh, onOpen,
}: {
  group: ModelGroup; current: boolean; accountAuthenticated: boolean; zh: boolean; onOpen(): void;
}) {
  const setup = providerSetup(group);
  const total = visibleProfiles(group).length;
  const available = selectableProfiles(group).length;
  const unavailable = total > 0 && available === 0;
  const adminBlocked = group.profiles.some((profile) => profile.available === false && String(profile.statusMessage || "").includes("Loom Admin"));
  const meta = group.id === "opencode-console" && accountAuthenticated && available > 0
    ? `${group.profiles.filter(isConsoleFree).length} OpenCode-only · ${total} models`
    : !accountAuthenticated
      ? "Sign in required"
    : adminBlocked
      ? "Admin blocked"
      : setup.needsKey
        ? "Not connected"
        : unavailable
          ? "Unavailable"
          : available < total
            ? `${available}/${total} ${zh ? "可用" : "available"}`
            : zh ? `${total} 个模型` : `${total} ${total === 1 ? "model" : "models"}`;
  return (
    <button type="button" className="cp-row" data-cp-item="" data-ic-row onClick={onOpen}>
      <ProviderMark id={group.id} name={group.name} />
      <span className="cp-row-text">
        <span className="cp-row-title">
          <span title={group.name}>{group.name}</span>
          {current ? <span className="cp-tag is-accent">{zh ? "当前" : "Current"}</span> : null}
        </span>
      </span>
      <span className={`cp-row-meta ${setup.needsKey || unavailable ? "is-attention" : ""}`}>{meta}</span>
      <span className="cp-row-end is-chevron" aria-hidden="true"><ChevronRight size={14} strokeWidth={1.75} /></span>
    </button>
  );
}

interface ModelRowProps {
  profile: ModelProfile;
  current: boolean;
  disabled: boolean;
  dimmed?: boolean;
  leading?: ReactNode;
  meta?: ReactNode;
  metaAttention?: boolean;
  end?: ReactNode;
  title?: string;
  onSelect(): void;
}

function ModelRow({ profile, current, disabled, dimmed, leading, meta, metaAttention, end, title, onSelect }: ModelRowProps) {
  return (
    <button
      type="button"
      className={`cp-row ${current ? "is-current" : ""} ${dimmed ? "is-dim" : ""}`}
      data-cp-item=""
      data-cp-current={current ? "" : undefined}
      data-ic-row
      aria-current={current ? "true" : undefined}
      disabled={disabled}
      title={title ?? profileTooltip(profile)}
      onClick={onSelect}
    >
      {leading}
      <span className="cp-row-text">
        <span className="cp-row-title"><span>{profile.name}</span></span>
        {modelIdAddsInfo(profile) ? <span className="cp-row-sub">{profile.model}</span> : null}
      </span>
      {meta ? <span className={`cp-row-meta ${metaAttention ? "is-attention" : ""}`}>{meta}</span> : null}
      <span className={`cp-row-end ${current && !end ? "is-check" : ""}`} aria-hidden="true">
        {end ?? (current ? <Check size={15} strokeWidth={2} /> : null)}
      </span>
    </button>
  );
}

interface ModelPanelProps {
  runtimeModel?: string;
  snapshot: ModelSnapshot | null;
  busy?: boolean;
  running?: boolean;
  accountAuthenticated?: boolean;
  onOpenAccount?(): void;
  onSwitchProfile(selection: string): Promise<void> | void;
  onSwitchCurrent(model: string): Promise<void> | void;
  onConfigureProvider(provider: string, apiKey: string): Promise<void> | void;
  onRefresh?(): Promise<void> | void;
  onAddModel(input: AddModelInput): Promise<void> | void;
  onDeleteModel(selection: string): Promise<void> | void;
  onReasoningChange(kind: string, value: string): Promise<void> | void;
  onClose(): void;
}

function endpointLabel(baseUrl: string): string {
  if (!baseUrl) return "OpenAI default endpoint";
  try {
    return new URL(baseUrl).host;
  } catch {
    return baseUrl;
  }
}

function adapterLabel(adapter: string): string {
  if (adapter === "openai") return "OpenAI";
  if (adapter === "opencode-go") return "OpenCode Go";
  return "OpenAI-compatible";
}

function activeReasoningOption(reasoning: ModelReasoningState): ModelReasoningOption | undefined {
  return reasoning.options.find((option) => option.value === reasoning.value);
}

export function ModelPanel({
  runtimeModel,
  snapshot,
  busy,
  running,
  accountAuthenticated = false,
  onOpenAccount,
  onSwitchProfile,
  onSwitchCurrent,
  onConfigureProvider,
  onRefresh,
  onAddModel,
  onDeleteModel,
  onReasoningChange,
  onClose,
}: ModelPanelProps) {
  const zh = useI18n().language === "zh-CN";
  const [view, setView] = useState<ModelView>("list");
  const [error, setError] = useState("");
  const [customModel, setCustomModel] = useState(runtimeModel || snapshot?.current?.model || "");
  const [name, setName] = useState("");
  const [adapter, setAdapter] = useState<AddModelInput["adapter"]>("openai-compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [pendingDelete, setPendingDelete] = useState("");
  const [selectedGroup, setSelectedGroup] = useState("");
  const [providerKey, setProviderKey] = useState("");
  const [providerConfiguring, setProviderConfiguring] = useState(false);
  const [query, setQuery] = useState("");
  const [groupQuery, setGroupQuery] = useState("");
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;

  useEffect(() => {
    // Opening the picker must never wait on provider discovery. Refresh in the
    // background; the existing snapshot remains interactive until the new
    // catalog arrives.
    void Promise.resolve(refreshRef.current?.()).catch(() => {
      // DesktopModelManager keeps the last-known-good catalog on transient
      // provider errors, so a background refresh failure is intentionally quiet.
    });
  }, []);

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
  const currentGroupName = snapshot?.current?.groupName || currentName;
  const currentGroupId = snapshot?.current ? profileGroupId(snapshot.current) : "current";
  const profiles = snapshot?.profiles ?? [];
  const groups = useMemo<ModelGroup[]>(() => {
    const grouped = new Map<string, ModelGroup>();
    for (const profile of profiles) {
      const id = profileGroupId(profile);
      const existing = grouped.get(id);
      if (existing) {
        existing.profiles.push(profile);
        continue;
      }
      grouped.set(id, {
        id,
        name: profile.groupName || (profile.kind === "saved" ? "Custom APIs" : profile.name),
        order: profile.groupOrder ?? 1000,
        profiles: [profile],
      });
    }
    return [...grouped.values()]
      .map((group) => ({
        ...group,
        profiles: [...group.profiles].sort((a, b) => {
          // Catch-all families read best at the end of the list.
          const other = Number(a.family === "Other") - Number(b.family === "Other");
          const family = String(a.family || "").localeCompare(String(b.family || ""));
          return other || family || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
        }),
      }))
      .sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
  }, [profiles]);
  const activeGroup = useMemo(
    () => groups.find((group) => group.id === selectedGroup) ?? null,
    [groups, selectedGroup],
  );
  const recent = useMemo(
    () => (snapshot?.recentModels ?? []).filter((item) => item && item !== currentModel).slice(0, 4),
    [currentModel, snapshot?.recentModels],
  );
  const recentProfiles = useMemo(
    () => recent
      .map((item) => profiles.find((profile) => profile.model === item && profile.available !== false))
      .filter((profile): profile is ModelProfile => Boolean(profile))
      .slice(0, 3),
    [profiles, recent],
  );
  const groupsById = useMemo(() => new Map(groups.map((group) => [group.id, group])), [groups]);
  const totalModels = useMemo(
    () => groups.reduce((sum, group) => sum + visibleProfiles(group).length, 0),
    [groups],
  );
  const searchResults = useMemo(() => {
    const tokens = queryTokens(query);
    if (!tokens.length) return null;
    const models = groups
      .map((group) => ({
        group,
        matches: visibleProfiles(group).filter((profile) => profileMatches(profile, group.name, tokens)),
      }))
      .filter((entry) => entry.matches.length > 0);
    const providers = groups.filter((group) => tokens.every((token) => group.name.toLowerCase().includes(token)));
    return { models, providers };
  }, [groups, query]);
  const locked = Boolean(busy);

  useEffect(() => {
    if (!confirmDelete) return;
    if (!profiles.some((profile) => profile.selection === confirmDelete)) setConfirmDelete("");
  }, [confirmDelete, profiles]);

  // Open a provider at the model in use rather than always at the top.
  const providerListRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (view !== "group") return;
    const list = providerListRef.current;
    if (!list) return;
    const row = list.querySelector<HTMLElement>(".cp-row.is-current");
    if (row) {
      const listBox = list.getBoundingClientRect();
      const rowBox = row.getBoundingClientRect();
      if (rowBox.top < listBox.top || rowBox.bottom > listBox.bottom) {
        list.scrollTop += rowBox.top - listBox.top - (listBox.height - rowBox.height) / 2;
      }
    }
    // Short providers have no search field to take focus; keep arrow keys working.
    const active = document.activeElement;
    if (!active || active === document.body) {
      (row ?? list.querySelector<HTMLElement>(ITEM))?.focus({ preventScroll: true });
    }
  }, [view, selectedGroup]);

  async function run(action: () => Promise<void> | void) {
    setError("");
    try {
      await action();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      // A policy change may have landed after the picker was opened.
      void Promise.resolve(refreshRef.current?.()).catch(() => {});
    }
  }

  function navigate(next: ModelView) {
    setView(next);
    setError("");
  }

  function openGroup(groupId: string) {
    setSelectedGroup(groupId);
    setGroupQuery("");
    setConfirmDelete("");
    navigate("group");
  }

  function isCurrentProfile(profile: ModelProfile): boolean {
    return profile.selection === currentSelection && profile.model === currentModel;
  }

  async function chooseProfile(profile: ModelProfile) {
    if (!accountAuthenticated) {
      setError("Sign in to Loom before using models.");
      return;
    }
    if (profile.available === false) {
      setError(profile.statusMessage || "This model is unavailable.");
      return;
    }
    // Picking the model already in use simply dismisses the picker.
    if (isCurrentProfile(profile)) {
      onClose();
      return;
    }
    // The owner keeps the request and any failure alive after this picker closes.
    const request = onSwitchProfile(profile.selection);
    onClose();
    void Promise.resolve(request).catch(() => {});
  }

  function reasoningLabel(profile: ModelProfile): string | undefined {
    return profile.reasoning ? activeReasoningOption(profile.reasoning)?.label : undefined;
  }

  async function submitCustom() {
    if (!accountAuthenticated) {
      setError("Sign in to Loom before using models.");
      onOpenAccount?.();
      return;
    }
    const value = customModel.trim();
    if (!value) {
      setError("Enter a model ID first.");
      return;
    }
    const request = onSwitchCurrent(value);
    onClose();
    void Promise.resolve(request).catch(() => {});
  }

  function openOwnKey(group: ModelGroup, profile?: ModelProfile) {
    if (!accountAuthenticated) {
      setError("Sign in to Loom before adding an API connection.");
      onOpenAccount?.();
      return;
    }
    const candidate = profile ?? visibleProfiles(group).find((item) => !item.setupOnly);
    if (!candidate) {
      setError("This provider does not expose a model template for a personal API connection.");
      return;
    }
    setName(`${group.name} personal`);
    const template = personalConnectionTemplate(candidate);
    setAdapter(template.adapter);
    setBaseUrl(template.baseUrl);
    setModel(candidate.model);
    setApiKey("");
    navigate("add");
  }

  async function configureProvider(provider: string) {
    if (!accountAuthenticated) {
      setError("Sign in to Loom before saving a provider credential.");
      onOpenAccount?.();
      return;
    }
    const key = providerKey.trim();
    if (!key || providerConfiguring) return;
    setError("");
    setProviderConfiguring(true);
    try {
      await onConfigureProvider(provider, key);
      setProviderKey("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setProviderConfiguring(false);
    }
  }

  async function submitAdd() {
    if (!accountAuthenticated) {
      setError("Sign in to Loom before adding an API connection.");
      onOpenAccount?.();
      return;
    }
    const input: AddModelInput = {
      name: name.trim(),
      adapter,
      baseUrl: adapter === "openai" ? "" : baseUrl.trim(),
      model: model.trim(),
      apiKey: apiKey.trim(),
    };
    const missing = [
      !input.name ? "name" : "",
      adapter === "openai-compatible" && !input.baseUrl ? "Base URL" : "",
      !input.model ? "model ID" : "",
      !input.apiKey ? "API key" : "",
    ].filter(Boolean);
    if (missing.length) {
      setError(`Fill in ${missing.join(", ")}.`);
      return;
    }
    await run(() => onAddModel(input));
  }

  async function deleteProfile(profile: ModelProfile) {
    if (profile.kind !== "saved" || locked || pendingDelete) return;
    if (confirmDelete !== profile.selection) {
      setError("");
      setConfirmDelete(profile.selection);
      return;
    }
    setError("");
    setPendingDelete(profile.selection);
    try {
      await onDeleteModel(profile.selection);
      setConfirmDelete("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPendingDelete("");
    }
  }

  const backToLibrary = zh ? "返回模型列表" : "Back to models";
  const signInNote = (copy: string) => (
    <div className="cp-note">
      <Lock size={13} strokeWidth={2} aria-hidden="true" />
      <span>{copy} <button type="button" className="cp-link" onClick={onOpenAccount}>{zh ? "登录" : "Sign in"}</button></span>
    </div>
  );
  const enterSubmits = (submit: () => void) => (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" || event.shiftKey || !(event.target instanceof HTMLInputElement)) return;
    event.preventDefault();
    event.stopPropagation();
    submit();
  };

  if (view === "add") {
    return (
      <div key="add" className="cp-view" role="form" onKeyDown={enterSubmits(() => void submitAdd())}>
        <PanelHead title={zh ? "添加连接" : "Add connection"} onBack={() => navigate("profiles")} backLabel={backToLibrary} />
        <div className="cp-body">
          {!accountAuthenticated ? signInNote("Sign in to Loom before saving or using an API connection.") : null}
          <div className="cp-form">
            <div className="cp-form-lead">Use your provider's API URL and key. Built-in models use your Loom account and need no personal connection.</div>
            <label className="cp-field">
              <span className="cp-field-label">{zh ? "连接名称" : "Connection name"}</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. MiniMax work API" autoFocus />
            </label>
            <div className="cp-field-row">
              <label className="cp-field">
                <span className="cp-field-label">{zh ? "接口类型" : "API type"}</span>
                <span className="cp-select">
                  <select value={adapter} onChange={(event) => setAdapter(event.target.value as AddModelInput["adapter"])}>
                    <option value="openai-compatible">OpenAI-compatible</option>
                    <option value="openai">OpenAI</option>
                  </select>
                  <ChevronDown size={14} strokeWidth={1.75} aria-hidden="true" />
                </span>
              </label>
              <label className="cp-field">
                <span className="cp-field-label">{zh ? "模型 ID" : "Model ID"}</span>
                <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="MiniMax-M3" spellCheck={false} />
              </label>
            </div>
            <label className="cp-field">
              <span className="cp-field-label">Base URL</span>
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={adapter === "openai" ? "OpenAI default endpoint" : "https://api.example.com/v1"}
                disabled={adapter === "openai"}
                spellCheck={false}
              />
            </label>
            <label className="cp-field">
              <span className="cp-field-label">API key</span>
              <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" />
              <span className="cp-field-hint">
                <KeyRound size={12} strokeWidth={1.75} aria-hidden="true" />
                {zh ? "保存在系统凭据库中。" : "Stored securely in your OS credential store."}
              </span>
            </label>
          </div>
          {error ? <div className="cp-note is-error" role="alert">{error}</div> : null}
        </div>
        <PanelFoot className="is-actions">
          <button type="button" className="cp-foot-action" onClick={() => navigate("profiles")}>{zh ? "取消" : "Cancel"}</button>
          <button type="button" className="cp-primary" disabled={locked || !accountAuthenticated} onClick={() => void submitAdd()}>
            {busy ? <LoaderCircle size={14} strokeWidth={2} /> : null}
            {zh ? "保存并使用" : "Save & use"}
          </button>
        </PanelFoot>
      </div>
    );
  }

  if (view === "custom") {
    return (
      <div key="custom" className="cp-view" role="form" onKeyDown={enterSubmits(() => void submitCustom())}>
        <PanelHead title={zh ? "自定义模型 ID" : "Custom model ID"} onBack={() => navigate("profiles")} backLabel={backToLibrary} />
        <div className="cp-body">
          <div className="cp-form">
            <div className="cp-form-lead">Keep the current connection and change only the model it calls.</div>
            <div className="cp-connection">
              <ProviderMark id={currentGroupId} name={currentGroupName} />
              <span className="cp-row-text">
                <span className="cp-row-title"><span>{currentName}</span></span>
                <span className="cp-row-sub">{adapterLabel(currentAdapter)} · {endpointLabel(currentBaseUrl)}</span>
              </span>
            </div>
            <label className="cp-field">
              <span className="cp-field-label">{zh ? "模型 ID" : "Model ID"}</span>
              <input
                value={customModel}
                onChange={(event) => setCustomModel(event.target.value)}
                placeholder="Model ID exposed by this API"
                autoFocus
                spellCheck={false}
              />
            </label>
            {recent.length ? (
              <div className="cp-field">
                <span className="cp-field-label">{zh ? "最近使用" : "Recent"}</span>
                <div className="cp-chips">
                  {recent.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={`cp-chip ${item === customModel.trim() ? "is-selected" : ""}`}
                      onClick={() => setCustomModel(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
          {error ? <div className="cp-note is-error" role="alert">{error}</div> : null}
        </div>
        <PanelFoot className="is-actions">
          <button type="button" className="cp-foot-action" onClick={() => navigate("profiles")}>{zh ? "取消" : "Cancel"}</button>
          <button type="button" className="cp-primary" disabled={locked || !accountAuthenticated} onClick={() => void submitCustom()}>
            {busy ? <LoaderCircle size={14} strokeWidth={2} /> : null}
            {zh ? "切换模型" : "Switch model"}
          </button>
        </PanelFoot>
      </div>
    );
  }

  if (view === "group" && activeGroup) {
    const sync = snapshot?.catalogSync?.providers.find(provider => provider.groupId === activeGroup.id);
    const syncTime = sync?.lastSuccess ? new Date(sync.lastSuccess * 1000).toLocaleString() : null;
    const setup = providerSetup(activeGroup);
    const availableChoices = selectableProfiles(activeGroup);
    const choices = visibleProfiles(activeGroup);
    const tokens = queryTokens(groupQuery);
    const visible = tokens.length ? choices.filter((profile) => profileMatches(profile, "", tokens)) : choices;
    const families = new Map<string, ModelProfile[]>();
    for (const profile of visible) {
      const family = profile.family || (activeGroup.id === "opencode-go" ? "Other" : "");
      const list = families.get(family) ?? [];
      list.push(profile);
      families.set(family, list);
    }
    const credentialTarget = setup.needsKey ? setup.credentialTarget : null;
    const showSearch = choices.length > 8 && !credentialTarget;
    const hasBuiltinModels = choices.some((profile) => profile.kind === "builtin");
    const blockedChoices = choices.filter((profile) => profile.available === false);
    const adminBlockedChoices = blockedChoices.filter(profile => String(profile.statusMessage || "").includes("Loom Admin"));

    return (
      <div key={`group:${activeGroup.id}`} className="cp-view">
        <PanelHead
          lead={<ProviderMark id={activeGroup.id} name={activeGroup.name} size="small" />}
          title={<span>{activeGroup.name}</span>}
          aside={!accountAuthenticated
            ? `${choices.length} models · sign in`
            : blockedChoices.length
              ? `${availableChoices.length}/${choices.length} ${zh ? "可用" : "available"}`
              : zh ? `${choices.length} 个模型` : `${choices.length} ${choices.length === 1 ? "model" : "models"}`}
          onBack={() => navigate("profiles")}
          backLabel={zh ? "返回供应商列表" : "Back to providers"}
        />
        {showSearch ? (
          <PanelSearch
            value={groupQuery}
            onChange={setGroupQuery}
            placeholder={zh ? `搜索 ${activeGroup.name}` : `Search ${activeGroup.name}`}
            label={`Search ${activeGroup.name} models`}
          />
        ) : null}

        <div className="cp-body" ref={providerListRef}>
          {snapshot?.catalogMetadataError ? <div className="cp-note" role="status">{snapshot.catalogMetadataError}</div> : null}
          {sync && accountAuthenticated ? (
            <div className="cp-note" role="status">
              <span>{sync.message || "Model directory received."} {syncTime ? `Last successful sync: ${syncTime}.` : "No successful provider sync recorded."} <button type="button" className="cp-link" onClick={() => { void Promise.resolve(refreshRef.current?.()).catch(() => {}); }}>Refresh</button></span>
            </div>
          ) : null}
          {!accountAuthenticated ? signInNote("Sign in to Loom to use any model.") : null}
          {adminBlockedChoices.length > 0 && accountAuthenticated ? (
            <div className="cp-note is-error">
              <Lock size={13} strokeWidth={2} aria-hidden="true" />
              <span>{adminBlockedChoices.length} built-in model{adminBlockedChoices.length === 1 ? " is" : "s are"} disabled by Loom Admin. You can still add a separate personal API connection below.</span>
            </div>
          ) : null}
          {activeGroup.id === "opencode-console" ? (
            <div className="cp-note" role="note">
              <span>OpenCode currently restricts free models to its own app (HTTP 403 from third-party clients). They cannot run in Loom. Console paid models need a separate Console key and admin approval; Go usage stays separate.</span>
            </div>
          ) : null}
          {hasBuiltinModels && accountAuthenticated && snapshot?.policySync?.state !== "fresh" ? (
            <div className="cp-note" role="status"><span>Model permissions are syncing. Last known states remain visible; the server checks access when a model runs. {snapshot?.policySync?.checkedAt ? `Last successful check: ${new Date(snapshot.policySync.checkedAt).toLocaleString()}.` : "No successful check yet."}</span></div>
          ) : null}
          {credentialTarget ? (
            <div className="cp-connect" role="form" onKeyDown={enterSubmits(() => void configureProvider(credentialTarget.provider))}>
              <div className="cp-connect-copy">
                <KeyRound size={15} strokeWidth={1.75} aria-hidden="true" />
                <div>
                  <div className="cp-connect-title">
                    {setup.catalogUnauthorized ? `Reconnect ${credentialTarget.label}` : `Connect ${credentialTarget.label}`}
                  </div>
                  <div className="cp-connect-hint">
                    {setup.catalogUnauthorized
                      ? "The saved credential was rejected by the provider. Enter the current group key to replace it securely."
                      : "The key is stored in your OS credential store and is never written into the model registry."}
                  </div>
                </div>
              </div>
              <div className="cp-connect-form">
                <input
                  type="password"
                  value={providerKey}
                  onChange={(event) => setProviderKey(event.target.value)}
                  placeholder={credentialTarget.placeholder}
                  aria-label={credentialTarget.placeholder}
                  autoComplete="off"
                  autoFocus
                  disabled={!accountAuthenticated}
                />
                <button type="button" className="cp-primary" disabled={!accountAuthenticated || !providerKey.trim() || providerConfiguring} onClick={() => void configureProvider(credentialTarget.provider)}>
                  {providerConfiguring ? <LoaderCircle size={14} strokeWidth={2} /> : null}
                  {zh ? "连接" : "Connect"}
                </button>
              </div>
            </div>
          ) : null}

          {error ? <div className="cp-note is-error" role="alert">{error}</div> : null}
          {setup.statusProfiles.map((profile) => (
            <div className="cp-note is-error" key={profile.id}>{profile.statusMessage}</div>
          ))}
          {running ? (
            <div className="cp-note">
              <Lock size={13} strokeWidth={2} aria-hidden="true" />
              <span>{zh ? "现在可以选择其他模型，从下一轮开始生效。" : "You can select another model now; it takes effect on the next turn."}</span>
            </div>
          ) : null}

          {[...families.entries()].map(([family, familyProfiles]) => (
            <section key={family || activeGroup.id} aria-label={family || activeGroup.name}>
              {family ? <PanelLabel><FamilyMark family={family} />{family}</PanelLabel> : null}
              {familyProfiles.map((profile) => {
                const current = isCurrentProfile(profile);
                const deletable = profile.kind === "saved";
                const deleting = pendingDelete === profile.selection;
                const confirming = confirmDelete === profile.selection;
                const row = (
                  <ModelRow
                    key={profile.selection}
                    profile={profile}
                    current={current}
                    disabled={!accountAuthenticated || profile.available === false || deleting || isConsoleFree(profile)}
                    dimmed={!accountAuthenticated || profile.available === false || setup.needsKey || isConsoleFree(profile)}
                    meta={!accountAuthenticated ? "Sign in" : profile.available === false ? unavailableLabel(profile) : profile.groupId === "opencode-console" ? modelPriceLabel(profile) : reasoningLabel(profile)}
                    metaAttention={!accountAuthenticated || profile.available === false || isConsoleFree(profile)}
                    end={!accountAuthenticated || profile.available === false || isConsoleFree(profile) ? <Lock size={13} strokeWidth={2} /> : undefined}
                    title={!accountAuthenticated ? "Sign in to Loom to use models" : isConsoleFree(profile) ? CONSOLE_FREE_RESTRICTION : profile.available === false ? unavailableReason(profile) : undefined}
                    onSelect={() => {
                      if (!current && setup.needsKey) {
                        setError(`Connect ${activeGroup.name} before selecting one of its models.`);
                        return;
                      }
                      void chooseProfile(profile);
                    }}
                  />
                );
                if (!deletable) return row;
                return (
                  <div key={profile.selection} className="cp-item">
                    {row}
                    <button
                      type="button"
                      className={`cp-icon ${confirming ? "is-confirming" : ""}`}
                      disabled={locked || Boolean(pendingDelete)}
                      title={confirming ? `Click again to delete ${profile.name}` : `Delete ${profile.name}`}
                      aria-label={confirming ? `Confirm delete ${profile.name}` : `Delete ${profile.name}`}
                      onClick={() => void deleteProfile(profile)}
                    >
                      {deleting ? <LoaderCircle size={14} strokeWidth={2} /> : <Trash2 size={14} strokeWidth={1.75} />}
                    </button>
                  </div>
                );
              })}
            </section>
          ))}

          {tokens.length && !visible.length ? (
            <div className="cp-empty">{zh ? `没有匹配“${groupQuery.trim()}”的模型` : `No models match “${groupQuery.trim()}”`}</div>
          ) : null}
          {confirmDelete ? (
            <div className="cp-note is-error">Click the trash icon again to delete this saved connection.</div>
          ) : null}

          {hasBuiltinModels ? (
            <>
              <hr className="cp-divider" />
              <button
                type="button"
                className="cp-row is-tall"
                data-cp-item=""
                data-ic-row
                title="Creates a separate personal connection stored in your OS credential store. It does not change or bypass the built-in Loom Admin policy."
                onClick={() => openOwnKey(activeGroup, visible[0])}
              >
                <span className="cp-row-lead"><KeyRound size={15} strokeWidth={1.75} aria-hidden="true" /></span>
                <span className="cp-row-text">
                  <span className="cp-row-title"><span>{zh ? "使用自己的 API Key" : "Use your own API key"}</span></span>
                  <span className="cp-row-desc">
                    {accountAuthenticated
                      ? (zh ? "单独的个人连接，保存在系统凭据库中，不改变 Loom 的模型策略。" : "A separate personal connection, kept in your OS credential store.")
                      : (zh ? "请先登录 Loom。" : "Sign in to Loom first.")}
                  </span>
                </span>
                <span className="cp-row-end is-chevron" aria-hidden="true"><ChevronRight size={14} strokeWidth={1.75} /></span>
              </button>
            </>
          ) : null}
        </div>
      </div>
    );
  }

  // A provider view whose group vanished (its last saved model was deleted)
  // falls back to the library as well.
  if (view === "profiles" || view === "group") {
    const currentProviderId = groups.find((group) => group.profiles.some(isCurrentProfile))?.id;
    const renderProviderRow = (group: ModelGroup) => (
      <ProviderRow
        key={group.id}
        group={group}
        current={group.id === currentProviderId}
        accountAuthenticated={accountAuthenticated}
        zh={zh}
        onOpen={() => openGroup(group.id)}
      />
    );

    return (
      <div key="profiles" className="cp-view">
        <PanelHead
          title={zh ? "模型" : "Models"}
          aside={zh
            ? `${groups.length} 个供应商 · ${totalModels} 个模型`
            : `${groups.length} ${groups.length === 1 ? "provider" : "providers"} · ${totalModels} models`}
          onBack={() => {
            setQuery("");
            navigate("list");
          }}
          backLabel={zh ? "返回" : "Back to model controls"}
        />
        <PanelSearch
          value={query}
          onChange={setQuery}
          placeholder={zh ? "搜索模型或供应商" : "Search models or providers"}
          label="Search models and providers"
        />

        <div className="cp-body">
          {error ? <div className="cp-note is-error" role="alert">{error}</div> : null}
          {snapshot?.catalogSync?.providers.some(provider => provider.state === "unsupported") ? (
            <div className="cp-note" role="status">Some providers require a newer Host. Supported providers continue syncing normally.</div>
          ) : null}
          {!accountAuthenticated ? signInNote("Sign in to Loom to use any model. The catalogue stays visible so you can see what is available.") : null}
          {running ? (
            <div className="cp-note">
              <Lock size={13} strokeWidth={2} aria-hidden="true" />
              <span>{zh ? "现在可以选择其他模型，从下一轮开始生效。" : "You can select another model now; it takes effect on the next turn."}</span>
            </div>
          ) : null}

          {searchResults ? (
            <>
              {searchResults.models.map(({ group, matches }) => {
                const needsKey = providerSetup(group).needsKey;
                return (
                  <section key={group.id} aria-label={group.name}>
                    <PanelLabel><ProviderMark id={group.id} name={group.name} size="small" />{group.name}</PanelLabel>
                    {matches.map((profile) => {
                      const current = isCurrentProfile(profile);
                      const connectFirst = needsKey && !current;
                      return (
                        <ModelRow
                          key={profile.selection}
                          profile={profile}
                          current={current}
                          disabled={!accountAuthenticated || profile.available === false || isConsoleFree(profile)}
                          dimmed={!accountAuthenticated || profile.available === false || isConsoleFree(profile)}
                          meta={!accountAuthenticated ? "Sign in" : profile.available === false ? unavailableLabel(profile) : connectFirst ? "Connect" : profile.groupId === "opencode-console" ? modelPriceLabel(profile) : reasoningLabel(profile)}
                          metaAttention={!accountAuthenticated || profile.available === false || connectFirst || isConsoleFree(profile)}
                          end={!accountAuthenticated || profile.available === false || isConsoleFree(profile) ? <Lock size={13} strokeWidth={2} /> : connectFirst ? <ChevronRight size={14} strokeWidth={1.75} /> : undefined}
                          title={!accountAuthenticated ? "Sign in to Loom to use models" : isConsoleFree(profile) ? CONSOLE_FREE_RESTRICTION : profile.available === false ? unavailableReason(profile) : connectFirst ? `Connect ${group.name} to use ${profile.name}` : undefined}
                          onSelect={() => (connectFirst ? openGroup(group.id) : void chooseProfile(profile))}
                        />
                      );
                    })}
                  </section>
                );
              })}
              {searchResults.providers.length ? (
                <section aria-label="Providers">
                  <PanelLabel>{zh ? "供应商" : "Providers"}</PanelLabel>
                  {searchResults.providers.map(renderProviderRow)}
                </section>
              ) : null}
              {!searchResults.models.length && !searchResults.providers.length ? (
                <div className="cp-empty">
                  <span>{zh ? `没有匹配“${query.trim()}”的模型` : `No models match “${query.trim()}”`}</span>
                  <button
                    type="button"
                    className="cp-link"
                    data-cp-item=""
                    onClick={() => {
                      setCustomModel(query.trim());
                      navigate("custom");
                    }}
                  >
                    {zh ? "作为自定义模型 ID 使用" : "Use it as a custom model ID"}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <>
              {recentProfiles.length ? (
                <section aria-label="Recent models">
                  <PanelLabel>{zh ? "最近使用" : "Recent"}</PanelLabel>
                  {recentProfiles.map((profile) => {
                    const groupId = profileGroupId(profile);
                    const group = groupsById.get(groupId);
                    const providerName = group?.name ?? profile.groupName ?? "";
                    const connectFirst = group ? providerSetup(group).needsKey : false;
                    return (
                      <ModelRow
                        key={profile.selection}
                        profile={profile}
                        current={false}
                        disabled={!accountAuthenticated || isConsoleFree(profile)}
                        dimmed={isConsoleFree(profile)}
                        leading={<ProviderMark id={groupId} name={providerName || profile.name} />}
                        meta={isConsoleFree(profile) ? modelPriceLabel(profile) : providerName}
                        end={connectFirst ? <KeyRound size={13} strokeWidth={1.75} /> : undefined}
                        title={isConsoleFree(profile) ? CONSOLE_FREE_RESTRICTION : connectFirst ? `Connect ${providerName} to use ${profile.name}` : undefined}
                        onSelect={() => (connectFirst ? openGroup(groupId) : void chooseProfile(profile))}
                      />
                    );
                  })}
                </section>
              ) : null}
              <section aria-label="Providers">
                <PanelLabel>{zh ? "供应商" : "Providers"}</PanelLabel>
                {groups.map(renderProviderRow)}
              </section>
            </>
          )}
        </div>

        <PanelFoot className="is-actions">
          <button type="button" className="cp-foot-action" onClick={() => accountAuthenticated ? navigate("add") : onOpenAccount?.()}>
            <Plus size={14} strokeWidth={1.75} aria-hidden="true" />
            <span>{zh ? "使用自己的 API Key" : "Use own API key"}</span>
          </button>
          <button type="button" className="cp-foot-action" onClick={() => accountAuthenticated ? navigate("custom") : onOpenAccount?.()}>
            <SlidersHorizontal size={14} strokeWidth={1.75} aria-hidden="true" />
            <span>{zh ? "自定义模型 ID" : "Custom model ID"}</span>
          </button>
        </PanelFoot>
      </div>
    );
  }

  return (
    <div key="list" className="cp-view">
      <button
        type="button"
        className="cp-identity composer-popover-head model-core-selector"
        aria-label={zh ? `选择模型，当前为 ${currentModel}` : `Choose model. Current model ${currentModel}`}
        onClick={() => navigate("profiles")}
      >
        <ProviderMark id={currentGroupId} name={currentGroupName} size="large" />
        <span className="cp-identity-text">
          <span className="cp-identity-name" title={currentModel}>{currentModel}</span>
          <span className="cp-identity-meta">
            {!accountAuthenticated
              ? (zh ? "需要登录" : "Sign in required")
              : <>
                  {currentGroupName}
                  {adapterLabel(currentAdapter) === currentGroupName ? null : ` · ${adapterLabel(currentAdapter)}`}
                </>}
          </span>
        </span>
        <span className="cp-identity-go" aria-hidden="true">
          {zh ? "更换" : "Change"}
          <ChevronRight size={14} strokeWidth={1.75} />
        </span>
      </button>

      <hr className="cp-divider" />

      <ReasoningControl
        reasoning={currentReasoning}
        busy={busy}
        running={running}
        onChange={onReasoningChange}
      />

      {error ? <div className="cp-note is-error" role="alert">{error}</div> : null}
    </div>
  );
}

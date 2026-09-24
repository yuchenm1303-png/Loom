import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  KeyRound,
  LoaderCircle,
  Lock,
  Plus,
  Search,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
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
import "./model-panel.css";
import { ReasoningThread } from "./ReasoningThread";

type ModelView = "list" | "profiles" | "group" | "add" | "custom";
type NavDirection = "forward" | "back";

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

function selectableProfiles(group: ModelGroup): ModelProfile[] {
  return group.profiles.filter((profile) => !profile.setupOnly);
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

// Provider marks are quiet tinted monograms. Hue and chroma feed OKLCH in
// model-picker.css, so every tint lands on the same perceived lightness.
const PROVIDER_TINTS: Record<string, readonly [hue: number, chroma: number]> = {
  minimax: [16, 0.14],
  deepseek: [262, 0.13],
  "opencode-go": [168, 0.1],
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

function ProviderMark({ id, name, small = false }: { id: string; name: string; small?: boolean }) {
  const [hue, chroma] = providerTint(id);
  const initial = name.trim().match(/[\p{L}\p{N}]/u)?.[0]?.toUpperCase() ?? "?";
  return (
    <span
      className={`mp-mark ${small ? "mp-mark-sm" : ""}`}
      style={{ "--mp-hue": hue, "--mp-chroma": chroma } as CSSProperties}
      aria-hidden="true"
    >
      {initial}
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

function profileTooltip(profile: ModelProfile): string {
  return [profile.name, profile.model === profile.name ? "" : profile.model, profile.protocol || ""]
    .filter(Boolean)
    .join(" · ");
}

const PICKER_ITEM = "[data-mp-item]:not(:disabled)";

function focusPickerItem(item: HTMLElement) {
  item.focus({ preventScroll: true });
  item.scrollIntoView({ block: "nearest" });
}

// Arrow keys walk the visible rows; ArrowUp from the first row returns to search.
function handlePickerKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const root = event.currentTarget;
  const items = Array.from(root.querySelectorAll<HTMLElement>(PICKER_ITEM));
  if (!items.length) return;
  event.preventDefault();
  const index = items.indexOf(document.activeElement as HTMLElement);
  if (event.key === "ArrowDown") {
    focusPickerItem(items[Math.min(items.length - 1, index + 1)]);
  } else if (index > 0) {
    focusPickerItem(items[index - 1]);
  } else {
    root.querySelector<HTMLInputElement>(".mp-search input")?.focus();
  }
}

function PickerHeader({
  title,
  meta,
  backLabel,
  onBack,
}: {
  title: ReactNode;
  meta?: ReactNode;
  backLabel: string;
  onBack(): void;
}) {
  return (
    <div className="mp-head">
      <button type="button" className="mp-back" onClick={onBack} aria-label={backLabel} title={backLabel}>
        <ChevronLeft size={17} strokeWidth={1.9} />
      </button>
      <div className="mp-head-title">{title}</div>
      {meta ? <div className="mp-head-meta">{meta}</div> : null}
    </div>
  );
}

function PickerSearch({
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
    <label className="mp-search">
      <Search size={15} strokeWidth={1.9} aria-hidden="true" />
      <input
        ref={inputRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            // First Escape clears the query; the next one closes the popover.
            event.preventDefault();
            event.stopPropagation();
            onChange("");
          } else if (event.key === "Enter" && value.trim()) {
            event.preventDefault();
            event.currentTarget.closest(".mp")?.querySelector<HTMLElement>(PICKER_ITEM)?.click();
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
          className="mp-search-clear"
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

function ProviderRow({ group, current, onOpen }: { group: ModelGroup; current: boolean; onOpen(): void }) {
  const setup = providerSetup(group);
  const count = selectableProfiles(group).length;
  const unavailable = count === 0 && setup.statusProfiles.length > 0;
  const meta = setup.needsKey
    ? "Not connected"
    : unavailable
      ? "Unavailable"
      : `${count} ${count === 1 ? "model" : "models"}`;
  return (
    <button
      type="button"
      className={`mp-row mp-provider ${current ? "is-current" : ""}`}
      data-mp-item=""
      onClick={onOpen}
    >
      <ProviderMark id={group.id} name={group.name} />
      <span className="mp-row-text">
        <span className="mp-row-line">
          <span className="mp-row-title" title={group.name}>{group.name}</span>
          {current ? <span className="mp-badge">Current</span> : null}
        </span>
      </span>
      <span className={`mp-row-meta ${setup.needsKey || unavailable ? "is-attention" : ""}`}>{meta}</span>
      <ChevronRight size={15} strokeWidth={1.9} className="mp-row-chevron" aria-hidden="true" />
    </button>
  );
}

interface ModelRowProps {
  profile: ModelProfile;
  current: boolean;
  pending: boolean;
  disabled: boolean;
  dimmed?: boolean;
  leading?: ReactNode;
  meta?: ReactNode;
  metaAttention?: boolean;
  end?: ReactNode;
  title?: string;
  onSelect(): void;
}

function ModelRow({
  profile,
  current,
  pending,
  disabled,
  dimmed,
  leading,
  meta,
  metaAttention,
  end,
  title,
  onSelect,
}: ModelRowProps) {
  return (
    <button
      type="button"
      className={`mp-row mp-model ${current ? "is-current" : ""} ${dimmed ? "is-dimmed" : ""}`}
      data-mp-item=""
      aria-current={current ? "true" : undefined}
      disabled={disabled}
      title={title ?? profileTooltip(profile)}
      onClick={onSelect}
    >
      {leading}
      <span className="mp-row-text">
        <span className="mp-row-title">{profile.name}</span>
        {modelIdAddsInfo(profile) ? <span className="mp-row-sub">{profile.model}</span> : null}
      </span>
      {meta ? <span className={`mp-row-meta ${metaAttention ? "is-attention" : ""}`}>{meta}</span> : null}
      <span className="mp-row-end" aria-hidden="true">
        {pending
          ? <LoaderCircle size={14} strokeWidth={2} className="mp-spin" />
          : end ?? (current ? <Check size={15} strokeWidth={2.2} /> : null)}
      </span>
    </button>
  );
}

interface ModelPanelProps {
  runtimeModel?: string;
  snapshot: ModelSnapshot | null;
  busy?: boolean;
  running?: boolean;
  onSwitchProfile(selection: string): Promise<void> | void;
  onSwitchCurrent(model: string): Promise<void> | void;
  onConfigureProvider(provider: string, apiKey: string): Promise<void> | void;
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

function profileSubtitle(profile: ModelProfile): string {
  if (profile.kind === "builtin") {
    if (profile.adapter === "opencode-go") {
      return `OpenCode Go · ${profile.family || "Model"} · ${profile.protocol || "auto"}`;
    }
    if (profile.groupId?.startsWith("managed-relay")) return `Built-in managed · ${endpointLabel(profile.baseUrl)}`;
    return `Built-in · ${endpointLabel(profile.baseUrl)}`;
  }
  return `${adapterLabel(profile.adapter)} · ${endpointLabel(profile.baseUrl)}`;
}

function activeReasoningOption(reasoning: ModelReasoningState): ModelReasoningOption | undefined {
  return reasoning.options.find((option) => option.value === reasoning.value);
}

export function ModelPanel({
  runtimeModel,
  snapshot,
  busy,
  running,
  onSwitchProfile,
  onSwitchCurrent,
  onConfigureProvider,
  onAddModel,
  onDeleteModel,
  onReasoningChange,
  onClose,
}: ModelPanelProps) {
  const [view, setView] = useState<ModelView>("list");
  const [direction, setDirection] = useState<NavDirection>("forward");
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
  const [pendingSelection, setPendingSelection] = useState("");

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
  const currentGroupName = snapshot?.current?.groupName || currentName;
  const currentFamily = snapshot?.current?.family || "";
  const currentProtocol = snapshot?.current?.protocol || "";
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
      .map((item) => profiles.find((profile) => profile.model === item))
      .filter((profile): profile is ModelProfile => Boolean(profile))
      .slice(0, 3),
    [profiles, recent],
  );
  const groupsById = useMemo(() => new Map(groups.map((group) => [group.id, group])), [groups]);
  const totalModels = useMemo(
    () => groups.reduce((sum, group) => sum + selectableProfiles(group).length, 0),
    [groups],
  );
  const searchResults = useMemo(() => {
    const tokens = queryTokens(query);
    if (!tokens.length) return null;
    const models = groups
      .map((group) => ({
        group,
        matches: selectableProfiles(group).filter((profile) => profileMatches(profile, group.name, tokens)),
      }))
      .filter((entry) => entry.matches.length > 0);
    const providers = groups.filter((group) => tokens.every((token) => group.name.toLowerCase().includes(token)));
    return { models, providers };
  }, [groups, query]);
  const locked = Boolean(busy || running);

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
    const row = list.querySelector<HTMLElement>(".mp-model.is-current");
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
      (row ?? list.querySelector<HTMLElement>(PICKER_ITEM))?.focus({ preventScroll: true });
    }
  }, [view, selectedGroup]);

  async function run(action: () => Promise<void> | void) {
    setError("");
    try {
      await action();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function navigate(next: ModelView, nextDirection: NavDirection = "forward") {
    setDirection(nextDirection);
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
    // Picking the model already in use simply dismisses the picker.
    if (isCurrentProfile(profile)) {
      onClose();
      return;
    }
    if (locked) return;
    setPendingSelection(profile.selection);
    try {
      await run(() => onSwitchProfile(profile.selection));
    } finally {
      setPendingSelection("");
    }
  }

  function reasoningLabel(profile: ModelProfile): string | undefined {
    return profile.reasoning ? activeReasoningOption(profile.reasoning)?.label : undefined;
  }

  async function submitCustom() {
    const value = customModel.trim();
    if (!value) {
      setError("Enter a model ID first.");
      return;
    }
    await run(() => onSwitchCurrent(value));
  }

  async function configureProvider(provider: string) {
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

  if (view === "add") {
    return (
      <form
        className="mp mp-form"
        data-direction={direction}
        onSubmit={(event) => {
          event.preventDefault();
          void submitAdd();
        }}
      >
        <PickerHeader title="Add connection" backLabel="Back to models" onBack={() => navigate("profiles", "back")} />
        <div className="mp-body mp-form-body">
          <div className="mp-form-lead">Connect OpenAI or any OpenAI-compatible endpoint.</div>
          <label className="mp-field">
            <span className="mp-field-label">Connection name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. MiniMax work API" autoFocus />
          </label>
          <div className="mp-field-row">
            <label className="mp-field">
              <span className="mp-field-label">API type</span>
              <span className="mp-select">
                <select value={adapter} onChange={(event) => setAdapter(event.target.value as AddModelInput["adapter"])}>
                  <option value="openai-compatible">OpenAI-compatible</option>
                  <option value="openai">OpenAI</option>
                </select>
                <ChevronDown size={14} strokeWidth={1.9} aria-hidden="true" />
              </span>
            </label>
            <label className="mp-field">
              <span className="mp-field-label">Model ID</span>
              <input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="MiniMax-M3"
                spellCheck={false}
              />
            </label>
          </div>
          <label className="mp-field">
            <span className="mp-field-label">Base URL</span>
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder={adapter === "openai" ? "OpenAI default endpoint" : "https://api.example.com/v1"}
              disabled={adapter === "openai"}
              spellCheck={false}
            />
          </label>
          <label className="mp-field">
            <span className="mp-field-label">API key</span>
            <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" />
            <span className="mp-field-hint">
              <KeyRound size={12} strokeWidth={1.9} aria-hidden="true" />
              Stored securely in your OS credential store.
            </span>
          </label>
          {error ? <div className="mp-notice is-error">{error}</div> : null}
        </div>
        <div className="mp-foot mp-form-foot">
          <button type="button" className="mp-foot-action" onClick={() => navigate("profiles", "back")}>Cancel</button>
          <button type="submit" className="mp-primary" disabled={locked}>
            {busy ? <LoaderCircle size={14} strokeWidth={2} className="mp-spin" /> : null}
            Save & use
          </button>
        </div>
      </form>
    );
  }

  if (view === "custom") {
    const connectionId = snapshot?.current ? profileGroupId(snapshot.current) : "current";
    return (
      <form
        className="mp mp-form"
        data-direction={direction}
        onSubmit={(event) => {
          event.preventDefault();
          void submitCustom();
        }}
      >
        <PickerHeader title="Custom model ID" backLabel="Back to models" onBack={() => navigate("profiles", "back")} />
        <div className="mp-body mp-form-body">
          <div className="mp-form-lead">Keep the current connection and change only the model it calls.</div>
          <div className="mp-connection">
            <ProviderMark id={connectionId} name={currentGroupName} />
            <span className="mp-row-text">
              <span className="mp-row-title">{currentName}</span>
              <span className="mp-connection-meta">{adapterLabel(currentAdapter)} · {endpointLabel(currentBaseUrl)}</span>
            </span>
          </div>
          <label className="mp-field">
            <span className="mp-field-label">Model ID</span>
            <input
              value={customModel}
              onChange={(event) => setCustomModel(event.target.value)}
              placeholder="Model ID exposed by this API"
              autoFocus
              spellCheck={false}
            />
          </label>
          {recent.length ? (
            <div className="mp-field">
              <span className="mp-field-label">Recent</span>
              <div className="mp-chips">
                {recent.map((item) => (
                  <button
                    key={item}
                    type="button"
                    className={`mp-chip ${item === customModel.trim() ? "is-selected" : ""}`}
                    onClick={() => setCustomModel(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {error ? <div className="mp-notice is-error">{error}</div> : null}
        </div>
        <div className="mp-foot mp-form-foot">
          <button type="button" className="mp-foot-action" onClick={() => navigate("profiles", "back")}>Cancel</button>
          <button type="submit" className="mp-primary" disabled={locked}>
            {busy ? <LoaderCircle size={14} strokeWidth={2} className="mp-spin" /> : null}
            Switch model
          </button>
        </div>
      </form>
    );
  }

  if (view === "group" && activeGroup) {
    const setup = providerSetup(activeGroup);
    const choices = selectableProfiles(activeGroup);
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

    return (
      <div className="mp mp-provider" data-direction={direction} onKeyDown={handlePickerKeyDown}>
        <PickerHeader
          title={(
            <>
              <ProviderMark id={activeGroup.id} name={activeGroup.name} small />
              <span>{activeGroup.name}</span>
            </>
          )}
          meta={`${choices.length} ${choices.length === 1 ? "model" : "models"}`}
          backLabel="Back to providers"
          onBack={() => navigate("profiles", "back")}
        />
        {showSearch ? (
          <PickerSearch
            value={groupQuery}
            onChange={setGroupQuery}
            placeholder={`Search ${activeGroup.name}`}
            label={`Search ${activeGroup.name} models`}
          />
        ) : null}

        <div className="mp-body" ref={providerListRef}>
          {credentialTarget ? (
            <form
              className="mp-connect"
              onSubmit={(event) => {
                event.preventDefault();
                void configureProvider(credentialTarget.provider);
              }}
            >
              <div className="mp-connect-copy">
                <span className="mp-connect-icon" aria-hidden="true"><KeyRound size={15} strokeWidth={1.9} /></span>
                <span className="mp-connect-text">
                  <span className="mp-connect-title">
                    {setup.catalogUnauthorized ? `Reconnect ${credentialTarget.label}` : `Connect ${credentialTarget.label}`}
                  </span>
                  <span className="mp-connect-hint">
                    {setup.catalogUnauthorized
                      ? "The saved credential was rejected by the provider. Enter the current group key to replace it securely."
                      : "The key is stored in your OS credential store and is never written into the model registry."}
                  </span>
                </span>
              </div>
              <div className="mp-connect-form">
                <input
                  type="password"
                  value={providerKey}
                  onChange={(event) => setProviderKey(event.target.value)}
                  placeholder={credentialTarget.placeholder}
                  aria-label={credentialTarget.placeholder}
                  autoComplete="off"
                  autoFocus
                />
                <button type="submit" disabled={!providerKey.trim() || providerConfiguring}>
                  {providerConfiguring ? <LoaderCircle size={14} strokeWidth={2} className="mp-spin" /> : null}
                  Connect
                </button>
              </div>
            </form>
          ) : null}

          {setup.statusProfiles.map((profile) => (
            <div className="mp-notice is-error" key={profile.id}>{profile.statusMessage}</div>
          ))}
          {running ? (
            <div className="mp-notice">
              <Lock size={13} strokeWidth={2} aria-hidden="true" />
              <span>Stop the active turn to switch models.</span>
            </div>
          ) : null}

          {[...families.entries()].map(([family, familyProfiles]) => (
            <section className="mp-section" key={family || activeGroup.id} aria-label={family || activeGroup.name}>
              {family ? <div className="mp-label">{family}</div> : null}
              {familyProfiles.map((profile) => {
                const current = isCurrentProfile(profile);
                const deletable = profile.kind === "saved";
                const deleting = pendingDelete === profile.selection;
                const confirming = confirmDelete === profile.selection;
                return (
                  <div
                    key={profile.selection}
                    className={`mp-item ${deletable ? "has-action" : ""} ${confirming ? "is-confirming" : ""}`}
                  >
                    <ModelRow
                      profile={profile}
                      current={current}
                      pending={pendingSelection === profile.selection}
                      disabled={deleting || (!current && (locked || setup.needsKey))}
                      dimmed={Boolean(running) || setup.needsKey}
                      meta={reasoningLabel(profile)}
                      onSelect={() => void chooseProfile(profile)}
                    />
                    {deletable ? (
                      <button
                        type="button"
                        className={`mp-item-action ${confirming ? "is-confirming" : ""}`}
                        disabled={locked || Boolean(pendingDelete)}
                        title={confirming ? `Click again to delete ${profile.name}` : `Delete ${profile.name}`}
                        aria-label={confirming ? `Confirm delete ${profile.name}` : `Delete ${profile.name}`}
                        onClick={() => void deleteProfile(profile)}
                      >
                        {deleting
                          ? <LoaderCircle size={14} strokeWidth={2} className="mp-spin" />
                          : <Trash2 size={14} strokeWidth={1.9} />}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </section>
          ))}

          {tokens.length && !visible.length ? (
            <div className="mp-empty">No models match “{groupQuery.trim()}”</div>
          ) : null}
          {confirmDelete ? (
            <div className="mp-notice is-error">Click the trash icon again to delete this saved connection.</div>
          ) : null}
          {error ? <div className="mp-notice is-error">{error}</div> : null}
        </div>
      </div>
    );
  }

  // A provider view whose group vanished (its last saved model was deleted)
  // falls back to the library as well.
  if (view === "profiles" || view === "group") {
    const currentGroupId = groups.find((group) => group.profiles.some(isCurrentProfile))?.id;
    const renderProviderRow = (group: ModelGroup) => (
      <ProviderRow
        key={group.id}
        group={group}
        current={group.id === currentGroupId}
        onOpen={() => openGroup(group.id)}
      />
    );

    return (
      <div className="mp mp-library" data-direction={direction} onKeyDown={handlePickerKeyDown}>
        <PickerHeader
          title="Models"
          meta={`${groups.length} ${groups.length === 1 ? "provider" : "providers"} · ${totalModels} models`}
          backLabel="Back to model controls"
          onBack={() => {
            setQuery("");
            navigate("list", "back");
          }}
        />
        <PickerSearch
          value={query}
          onChange={setQuery}
          placeholder="Search models or providers"
          label="Search models and providers"
        />

        <div className="mp-body">
          {running ? (
            <div className="mp-notice">
              <Lock size={13} strokeWidth={2} aria-hidden="true" />
              <span>Stop the active turn to switch models.</span>
            </div>
          ) : null}

          {searchResults ? (
            <>
              {searchResults.models.map(({ group, matches }) => {
                const needsKey = providerSetup(group).needsKey;
                return (
                  <section className="mp-section" key={group.id} aria-label={group.name}>
                    <div className="mp-label mp-label-provider">
                      <ProviderMark id={group.id} name={group.name} small />
                      <span>{group.name}</span>
                    </div>
                    {matches.map((profile) => {
                      const current = isCurrentProfile(profile);
                      const connectFirst = needsKey && !current;
                      return (
                        <ModelRow
                          key={profile.selection}
                          profile={profile}
                          current={current}
                          pending={pendingSelection === profile.selection}
                          disabled={!current && locked}
                          dimmed={Boolean(running)}
                          meta={connectFirst ? "Connect" : reasoningLabel(profile)}
                          metaAttention={connectFirst}
                          end={connectFirst ? <ChevronRight size={14} strokeWidth={1.9} /> : undefined}
                          title={connectFirst ? `Connect ${group.name} to use ${profile.name}` : undefined}
                          onSelect={() => (connectFirst ? openGroup(group.id) : void chooseProfile(profile))}
                        />
                      );
                    })}
                  </section>
                );
              })}
              {searchResults.providers.length ? (
                <section className="mp-section" aria-label="Providers">
                  <div className="mp-label">Providers</div>
                  {searchResults.providers.map(renderProviderRow)}
                </section>
              ) : null}
              {!searchResults.models.length && !searchResults.providers.length ? (
                <div className="mp-empty">
                  <span>No models match “{query.trim()}”</span>
                  <button
                    type="button"
                    className="mp-link"
                    data-mp-item=""
                    onClick={() => {
                      setCustomModel(query.trim());
                      navigate("custom");
                    }}
                  >
                    Use it as a custom model ID
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <>
              {recentProfiles.length ? (
                <section className="mp-section" aria-label="Recent models">
                  <div className="mp-label">Recent</div>
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
                        pending={pendingSelection === profile.selection}
                        disabled={locked}
                        dimmed={Boolean(running)}
                        leading={<ProviderMark id={groupId} name={providerName || profile.name} small />}
                        meta={providerName}
                        end={connectFirst ? <KeyRound size={13} strokeWidth={1.9} /> : undefined}
                        title={connectFirst ? `Connect ${providerName} to use ${profile.name}` : undefined}
                        onSelect={() => (connectFirst ? openGroup(groupId) : void chooseProfile(profile))}
                      />
                    );
                  })}
                </section>
              ) : null}
              <section className="mp-section" aria-label="Providers">
                <div className="mp-label">Providers</div>
                {groups.map(renderProviderRow)}
              </section>
            </>
          )}

          {error ? <div className="mp-notice is-error">{error}</div> : null}
        </div>

        <div className="mp-foot">
          <button type="button" className="mp-foot-action" onClick={() => navigate("add")}>
            <Plus size={14} strokeWidth={2} aria-hidden="true" />
            <span>Add connection</span>
          </button>
          <button type="button" className="mp-foot-action" onClick={() => navigate("custom")}>
            <SlidersHorizontal size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>Custom model ID</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="model-manager-view model-manager-home model-core-home" data-direction={direction}>
      <button
        type="button"
        className={`model-core-identity model-core-selector ${locked ? "locked" : ""}`}
        aria-label={`Choose model. Current model ${currentModel}`}
        disabled={locked}
        onClick={() => navigate("profiles")}
      >
        <div className="model-core-copy" key={`${currentSelection}:${currentModel}`}>
          <span className="model-core-title-row">
            <strong title={currentModel}>{currentModel}</strong>
            <ChevronRight size={13} strokeWidth={1.8} aria-hidden="true" />
          </span>
          <small>
            {currentGroupName}
            {adapterLabel(currentAdapter) === currentGroupName ? null : ` · ${adapterLabel(currentAdapter)}`}
          </small>
        </div>
      </button>

      <ReasoningThread
        reasoning={currentReasoning}
        busy={busy}
        running={running}
        onChange={onReasoningChange}
      />

      {error ? <div className="composer-popover-error">{error}</div> : null}
    </div>
  );

}
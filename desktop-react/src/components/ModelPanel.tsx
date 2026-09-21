import {
  ArrowLeft,
  BrainCircuit,
  Check,
  ChevronRight,
  Cpu,
  KeyRound,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import type {
  AddModelInput,
  ModelProfile,
  ModelReasoningOption,
  ModelReasoningState,
  ModelSnapshot,
} from "../types/loom";
import "./model-panel.css";

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

function builtinBadge(profile: ModelProfile): string | null {
  if (profile.kind !== "builtin") return null;
  if (profile.selection === "builtin:minimax") return "Primary";
  if (profile.selection.startsWith("builtin:deepseek")) return "DeepSeek";
  if (profile.selection.startsWith("builtin:opencode-go:")) return "Go";
  return "Managed";
}

function activeReasoningOption(reasoning: ModelReasoningState): ModelReasoningOption | undefined {
  return reasoning.options.find((option) => option.value === reasoning.value);
}

function ReasoningControl({
  reasoning,
  busy,
  running,
  onChange,
}: {
  reasoning: ModelReasoningState;
  busy?: boolean;
  running?: boolean;
  onChange(kind: string, value: string): Promise<void> | void;
}) {
  const selectedIndex = Math.max(0, reasoning.options.findIndex((option) => option.value === reasoning.value));
  const [displayIndex, setDisplayIndex] = useState(selectedIndex);
  const [error, setError] = useState("");
  const locked = Boolean(busy || running);
  const displayOption = reasoning.options[displayIndex] ?? reasoning.options[selectedIndex] ?? reasoning.options[0];
  const canReset = reasoning.value !== reasoning.defaultValue;
  const optionCount = Math.max(1, reasoning.options.length);
  const threadProgress = reasoning.options.length <= 1
    ? 50
    : 8 + (displayIndex / Math.max(1, reasoning.options.length - 1)) * 84;
  const threadStyle = {
    "--reasoning-index": String(displayIndex),
    "--reasoning-count": String(optionCount),
    "--reasoning-progress": `${threadProgress}%`,
  } as CSSProperties;

  useEffect(() => {
    setDisplayIndex(selectedIndex);
  }, [selectedIndex]);

  async function commit(index: number) {
    const option = reasoning.options[index];
    if (!option || locked || option.value === reasoning.value) return;
    setError("");
    setDisplayIndex(index);
    try {
      await onChange(reasoning.kind, option.value);
    } catch (cause) {
      setDisplayIndex(selectedIndex);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function reset() {
    const index = reasoning.options.findIndex((option) => option.value === reasoning.defaultValue);
    if (index >= 0) await commit(index);
  }

  return (
    <div className={`reasoning-control ${locked ? "locked" : ""}`}>
      <div className="reasoning-control-head">
        <span className="reasoning-control-eyebrow">
          <BrainCircuit size={13.5} strokeWidth={1.8} />
          Reasoning
        </span>
        {canReset ? (
          <button
            type="button"
            className="reasoning-reset"
            disabled={locked}
            onClick={() => void reset()}
            title="Reset reasoning"
            aria-label="Reset reasoning"
          >
            <RotateCcw size={12.5} />
            <span>Reset</span>
          </button>
        ) : null}
      </div>

      <div className="reasoning-thread-shell" style={threadStyle}>
        <div className="reasoning-thread-lines" aria-hidden="true">
          <svg viewBox="0 0 100 18" preserveAspectRatio="none">
            <path className="reasoning-thread-path reasoning-thread-path-a" d="M1 9 C13 2.6 24 15.4 38 9 S63 2.6 77 9 S91 14 99 9" />
            <path className="reasoning-thread-path reasoning-thread-path-b" d="M1 9 C13 15.4 24 2.6 38 9 S63 15.4 77 9 S91 4 99 9" />
          </svg>
          <div className="reasoning-thread-energy">
            <svg viewBox="0 0 100 18" preserveAspectRatio="none">
              <path className="reasoning-thread-path reasoning-thread-path-a" d="M1 9 C13 2.6 24 15.4 38 9 S63 2.6 77 9 S91 14 99 9" />
              <path className="reasoning-thread-path reasoning-thread-path-b" d="M1 9 C13 15.4 24 2.6 38 9 S63 15.4 77 9 S91 4 99 9" />
              <path className="reasoning-thread-path reasoning-thread-glint reasoning-thread-glint-a" d="M1 9 C13 2.6 24 15.4 38 9 S63 2.6 77 9 S91 14 99 9" />
              <path className="reasoning-thread-path reasoning-thread-glint reasoning-thread-glint-b" d="M1 9 C13 15.4 24 2.6 38 9 S63 15.4 77 9 S91 4 99 9" />
            </svg>
          </div>
        </div>

        <div className="reasoning-thread-nodes" role="group" aria-label="Reasoning effort">
          {reasoning.options.map((option, index) => (
            <button
              key={option.value}
              type="button"
              className={`reasoning-thread-node ${index === displayIndex ? "active" : ""}`}
              disabled={locked || reasoning.options.length <= 1}
              aria-pressed={index === displayIndex}
              title={option.description}
              onClick={() => void commit(index)}
            >
              <i aria-hidden="true" />
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      </div>

      {running ? <div className="reasoning-locked-note">Stop the active turn to change reasoning.</div> : null}
      {error ? <div className="composer-popover-error">{error}</div> : null}
    </div>

  );
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

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
  const currentReasoningOption = currentReasoning ? activeReasoningOption(currentReasoning) : null;
  const currentGroupName = snapshot?.current?.groupName || currentName;
  const currentFamily = snapshot?.current?.family || "";
  const currentProtocol = snapshot?.current?.protocol || "";
  const profiles = snapshot?.profiles ?? [];
  const groups = useMemo<ModelGroup[]>(() => {
    const grouped = new Map<string, ModelGroup>();
    for (const profile of profiles) {
      const id = profile.groupId || profile.selection;
      const existing = grouped.get(id);
      if (existing) {
        existing.profiles.push(profile);
        continue;
      }
      grouped.set(id, {
        id,
        name: profile.groupName || profile.name,
        order: profile.groupOrder ?? 1000,
        profiles: [profile],
      });
    }
    return [...grouped.values()]
      .map((group) => ({
        ...group,
        profiles: [...group.profiles].sort((a, b) => {
          const family = String(a.family || "").localeCompare(String(b.family || ""));
          return family || a.name.localeCompare(b.name);
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
  const normalizedQuery = query.trim().toLowerCase();
  const visibleGroups = useMemo(
    () => normalizedQuery
      ? groups.filter((group) => (
          group.name.toLowerCase().includes(normalizedQuery)
          || group.profiles.some((profile) => [
            profile.name,
            profile.model,
            profile.family || "",
            profile.protocol || "",
          ].some((value) => value.toLowerCase().includes(normalizedQuery)))
        ))
      : groups,
    [groups, normalizedQuery],
  );
  const locked = Boolean(busy || running);

  useEffect(() => {
    if (!confirmDelete) return;
    if (!profiles.some((profile) => profile.selection === confirmDelete)) setConfirmDelete("");
  }, [confirmDelete, profiles]);

  async function run(action: () => Promise<void> | void) {
    setError("");
    try {
      await action();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
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
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("list"); setError(""); }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><Plus size={17} /></span>
          <div>
            <strong>Add API / model</strong>
            <span>Connect OpenAI or an OpenAI-compatible endpoint.</span>
          </div>
        </div>

        <div className="model-form-grid">
          <label className="model-field model-field-wide">
            <span>Connection name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. MiniMax work API" autoFocus />
          </label>
          <label className="model-field">
            <span>API type</span>
            <select value={adapter} onChange={(event) => setAdapter(event.target.value as AddModelInput["adapter"])}>
              <option value="openai-compatible">OpenAI-compatible</option>
              <option value="openai">OpenAI</option>
            </select>
          </label>
          <label className="model-field">
            <span>Model ID</span>
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="MiniMax-M3" />
          </label>
          <label className="model-field model-field-wide">
            <span>Base URL</span>
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder={adapter === "openai" ? "OpenAI default endpoint" : "https://api.example.com/v1"}
              disabled={adapter === "openai"}
            />
          </label>
          <label className="model-field model-field-wide">
            <span>API key</span>
            <div className="model-secret-input">
              <KeyRound size={14} />
              <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Stored securely in the OS credential store" />
            </div>
          </label>
        </div>

        <div className="model-security-note">
          <KeyRound size={13} />
          <span>API keys stay in the operating-system credential store.</span>
        </div>
        {error ? <div className="composer-popover-error">{error}</div> : null}
        <div className="model-form-actions">
          <button type="button" className="model-secondary-button" onClick={() => setView("list")}>Cancel</button>
          <button type="button" className="model-primary-button" disabled={locked} onClick={() => void submitAdd()}>
            {busy ? <RefreshCw size={14} className="model-spin" /> : <Plus size={14} />}
            Save & use
          </button>
        </div>
      </div>
    );
  }

  if (view === "custom") {
    return (
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("profiles"); setError(""); }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><SlidersHorizontal size={17} /></span>
          <div>
            <strong>Other model ID</strong>
            <span>Keep the same API connection and change only the model name.</span>
          </div>
        </div>
        <div className="model-connection-summary">
          <Server size={15} />
          <div>
            <strong>{currentName}</strong>
            <span>{adapterLabel(currentAdapter)} · {endpointLabel(currentBaseUrl)}</span>
          </div>
        </div>
        <label className="model-field model-field-wide">
          <span>Model ID</span>
          <input value={customModel} onChange={(event) => setCustomModel(event.target.value)} placeholder="Model ID exposed by this API" autoFocus />
        </label>
        {recent.length ? (
          <div className="model-recent-row">
            <span>Recent</span>
            <div>{recent.map((item) => <button key={item} type="button" onClick={() => setCustomModel(item)}>{item}</button>)}</div>
          </div>
        ) : null}
        {error ? <div className="composer-popover-error">{error}</div> : null}
        <div className="model-form-actions">
          <button type="button" className="model-secondary-button" onClick={() => setView("profiles")}>Cancel</button>
          <button type="button" className="model-primary-button" disabled={locked} onClick={() => void submitCustom()}>
            {busy ? <RefreshCw size={14} className="model-spin" /> : <RefreshCw size={14} />}
            Switch model
          </button>
        </div>
      </div>
    );
  }

  if (view === "group" && activeGroup) {
    const selectableProfiles = activeGroup.profiles.filter((profile) => !profile.setupOnly);
    const statusProfiles = activeGroup.profiles.filter((profile) => profile.setupOnly && profile.statusMessage);
    const families = new Map<string, ModelProfile[]>();
    for (const profile of selectableProfiles) {
      const family = profile.family || (activeGroup.id === "opencode-go" ? "Other" : "");
      const list = families.get(family) ?? [];
      list.push(profile);
      families.set(family, list);
    }
    const familyEntries = [...families.entries()];
    const credentialTarget = providerCredentialTarget(activeGroup.id);
    const catalogUnauthorized = statusProfiles.some(
      (profile) => profile.statusMessage?.includes("HTTP 401"),
    );
    const needsProviderKey = Boolean(
      credentialTarget
      && (
        activeGroup.profiles.every((profile) => profile.configured === false)
        || catalogUnauthorized
      )
    );

    return (
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("profiles"); setError(""); }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><Server size={17} /></span>
          <div>
            <strong>{activeGroup.name}</strong>
            <span>{selectableProfiles.length} models · choose the exact model for this conversation.</span>
          </div>
        </div>

        {needsProviderKey ? (
          <div className="model-provider-connect">
            <div className="model-provider-connect-copy">
              <KeyRound size={16} />
              <div>
                <strong>{catalogUnauthorized ? `Reconnect ${credentialTarget?.label}` : `Connect ${credentialTarget?.label}`}</strong>
                <span>
                  {catalogUnauthorized
                    ? "The saved credential was rejected by the provider. Enter the current group key to replace it securely."
                    : "The key is stored in your OS credential store and is never written into the model registry."}
                </span>
              </div>
            </div>
            <div className="model-provider-connect-form">
              <input
                type="password"
                value={providerKey}
                onChange={(event) => setProviderKey(event.target.value)}
                placeholder={credentialTarget?.placeholder || "Provider key"}
                autoComplete="off"
              />
              <button
                type="button"
                disabled={!providerKey.trim() || providerConfiguring}
                onClick={() => credentialTarget && void configureProvider(credentialTarget.provider)}
              >
                {providerConfiguring ? <RefreshCw size={13} className="model-spin" /> : <KeyRound size={13} />}
                Connect
              </button>
            </div>
          </div>
        ) : null}

        {statusProfiles.map((profile) => (
          <div className="composer-popover-error" key={profile.id}>
            {profile.statusMessage}
          </div>
        ))}

        <div className="model-group-models">
          {familyEntries.map(([family, familyProfiles]) => (
            <section className="model-family-section" key={family || activeGroup.id}>
              {family ? <div className="model-family-heading">{family}</div> : null}
              <div className="model-profile-list">
                {familyProfiles.map((profile) => {
                  const exactActive = profile.selection === currentSelection && profile.model === currentModel;
                  const profileReasoning = profile.reasoning ? activeReasoningOption(profile.reasoning) : null;
                  const badge = builtinBadge(profile);
                  const deletable = profile.kind === "saved";
                  const deleting = pendingDelete === profile.selection;
                  const confirming = confirmDelete === profile.selection;
                  return (
                    <div
                      key={profile.selection}
                      className={`model-profile-row ${exactActive ? "active" : ""} ${deletable ? "deletable" : ""} ${confirming ? "confirm-delete" : ""}`}
                    >
                      <button
                        type="button"
                        className="model-profile-main"
                        disabled={locked || exactActive || deleting || needsProviderKey}
                        onClick={() => void run(() => onSwitchProfile(profile.selection))}
                      >
                        <span className={`model-profile-icon ${profile.kind}`}><Server size={15} /></span>
                        <span className="model-profile-copy">
                          <span className="model-profile-title-row">
                            <strong title={profile.name}>{profile.name}</strong>
                            {badge ? <em>{badge}</em> : null}
                            {profileReasoning ? <em className="model-reasoning-badge">{profileReasoning.label}</em> : null}
                          </span>
                          <span title={profile.model}>{profile.model}</span>
                          <small title={profileSubtitle(profile)}>{profileSubtitle(profile)}</small>
                        </span>
                        <span className="model-profile-action">
                          {exactActive ? <Check size={14} /> : busy ? <RefreshCw size={13} className="model-spin" /> : <ChevronRight size={14} />}
                        </span>
                      </button>
                      {deletable ? (
                        <button
                          type="button"
                          className={`model-profile-delete ${confirming ? "confirm" : ""}`}
                          disabled={locked || Boolean(pendingDelete)}
                          title={confirming ? `Click again to delete ${profile.name}` : `Delete ${profile.name}`}
                          aria-label={confirming ? `Confirm delete ${profile.name}` : `Delete ${profile.name}`}
                          onClick={() => void deleteProfile(profile)}
                        >
                          {deleting ? <RefreshCw size={13} className="model-spin" /> : <Trash2 size={13} />}
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>

        {confirmDelete ? <div className="model-delete-note">Click the trash icon again to delete this saved API connection.</div> : null}
        {error ? <div className="composer-popover-error">{error}</div> : null}
      </div>
    );
  }

  if (view === "profiles") {
    return (
      <div className="model-manager-view model-library-view">
        <div className="model-layer-header">
          <button
            type="button"
            className="model-back model-layer-back"
            onClick={() => { setView("list"); setError(""); setQuery(""); }}
            aria-label="Back to model controls"
          >
            <ArrowLeft size={14} />
          </button>
          <div>
            <strong>Models</strong>
            <span>{groups.length} providers · {profiles.length} available</span>
          </div>
        </div>

        <label className="model-library-search">
          <Search size={13.5} strokeWidth={1.8} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search providers or models"
            aria-label="Search models"
          />
        </label>

        {!normalizedQuery && recentProfiles.length ? (
          <section className="model-library-section model-library-recent">
            <div className="model-library-label">RECENT</div>
            <div className="model-recent-models">
              {recentProfiles.map((profile) => (
                <button
                  key={profile.selection}
                  type="button"
                  disabled={locked}
                  onClick={() => void run(() => onSwitchProfile(profile.selection))}
                >
                  <span className="model-recent-dot" aria-hidden="true" />
                  <span>
                    <strong title={profile.model}>{profile.model}</strong>
                    <small title={profile.groupName || profile.name}>{profile.groupName || profile.name}</small>
                  </span>
                  <ChevronRight size={13} />
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="model-library-section">
          <div className="model-library-label">PROVIDERS</div>
          <div className="model-profile-list model-group-list">
            {visibleGroups.map((group) => {
              const active = group.profiles.some(
                (profile) => profile.selection === currentSelection && profile.model === currentModel,
              );
              const credentialTarget = providerCredentialTarget(group.id);
              const selectableCount = group.profiles.filter((profile) => !profile.setupOnly).length;
              const statusProfile = group.profiles.find((profile) => profile.setupOnly && profile.statusMessage);
              const authRejected = Boolean(statusProfile?.statusMessage?.includes("HTTP 401"));
              const connected = (
                !credentialTarget
                || (!authRejected && group.profiles.some((profile) => profile.configured !== false))
              );
              return (
                <div key={group.id} className={`model-profile-row model-group-row ${active ? "active" : ""}`}>
                  <button
                    type="button"
                    className="model-profile-main"
                    disabled={locked}
                    onClick={() => {
                      setSelectedGroup(group.id);
                      setView("group");
                      setError("");
                    }}
                  >
                    <span className="model-profile-icon builtin"><Server size={15} /></span>
                    <span className="model-profile-copy">
                      <span className="model-profile-title-row">
                        <strong title={group.name}>{group.name}</strong>
                        {active ? <em>Current</em> : null}
                      </span>
                      <span>{selectableCount} {selectableCount === 1 ? "model" : "models"}</span>
                      <small>
                        {statusProfile
                          ? `${endpointLabel(statusProfile.baseUrl)} · ${authRejected ? "authentication required" : "catalog unavailable"}`
                          : credentialTarget
                            ? (connected ? `${credentialTarget.label} · connected` : `${credentialTarget.label} · key required`)
                            : group.profiles[0]?.kind === "saved"
                              ? profileSubtitle(group.profiles[0])
                              : "Built-in provider"}
                      </small>
                    </span>
                    <span className="model-profile-action"><ChevronRight size={14} /></span>
                  </button>
                </div>
              );
            })}
          </div>
          {normalizedQuery && visibleGroups.length === 0 ? (
            <div className="model-library-empty">No matching providers or models.</div>
          ) : null}
        </section>

        <button
          type="button"
          className="model-library-add"
          onClick={() => { setView("add"); setError(""); }}
          disabled={locked}
        >
          <Plus size={14} />
          <span><strong>Add connection</strong><small>Custom API or model endpoint</small></span>
          <ChevronRight size={13} />
        </button>

        <button
          type="button"
          className="model-library-custom"
          onClick={() => { setView("custom"); setError(""); }}
          disabled={locked}
        >
          <SlidersHorizontal size={13.5} />
          Use another model ID on the current connection
        </button>

        {error ? <div className="composer-popover-error">{error}</div> : null}
      </div>
    );
  }

  return (
    <div className="model-manager-view model-manager-home model-core-home">
      <button
        type="button"
        className={`model-core-identity model-core-selector ${locked ? "locked" : ""}`}
        aria-label={`Choose model. Current model ${currentModel}`}
        disabled={locked}
        onClick={() => { setView("profiles"); setError(""); }}
      >
        <div className="model-core-copy" key={`${currentSelection}:${currentModel}`}>
          <span className="model-core-title-row">
            <strong title={currentModel}>{currentModel}</strong>
            <ChevronRight size={13} strokeWidth={1.8} aria-hidden="true" />
          </span>
          <small>
            {currentGroupName} · {adapterLabel(currentAdapter)}
            {currentReasoningOption ? ` · ${currentReasoningOption.label}` : " · Auto"}
          </small>
        </div>
      </button>

      <section className="model-core-reasoning" aria-label="Reasoning control">
        {currentReasoning ? (
          <ReasoningControl
            reasoning={currentReasoning}
            busy={busy}
            running={running}
            onChange={onReasoningChange}
          />
        ) : (
          <div className="reasoning-managed">
            <div className="reasoning-control-head">
              <span className="reasoning-control-eyebrow">
                <BrainCircuit size={13.5} strokeWidth={1.8} />
                Reasoning
              </span>
            </div>
            <div className="reasoning-managed-thread" aria-hidden="true">
              <span />
              <i />
              <span />
            </div>
            <div className="reasoning-managed-label">Auto</div>
          </div>
        )}
      </section>

      {error ? <div className="composer-popover-error">{error}</div> : null}
    </div>
  );

}
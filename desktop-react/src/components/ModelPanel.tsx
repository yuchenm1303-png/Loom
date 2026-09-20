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
    if (profile.baseUrl.includes("relay.smirel.com")) return "Built-in managed · Smirel Relay";
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
  const segmentStyle = {
    "--reasoning-index": String(displayIndex),
    "--reasoning-count": String(Math.max(1, reasoning.options.length)),
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
        <div className="reasoning-control-title">
          <span className="reasoning-control-icon"><BrainCircuit size={14} strokeWidth={1.9} /></span>
          <div>
            <strong>Reasoning</strong>
            <span key={displayOption?.value || reasoning.value} className="reasoning-control-description">
              {displayOption?.description || "Choose how much reasoning time Loom should spend before answering."}
            </span>
          </div>
        </div>
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

      <div className="reasoning-segments" style={segmentStyle} role="group" aria-label="Reasoning effort">
        {reasoning.options.map((option, index) => (
          <button
            key={option.value}
            type="button"
            className={`reasoning-segment ${index === displayIndex ? "active" : ""}`}
            disabled={locked || reasoning.options.length <= 1}
            aria-pressed={index === displayIndex}
            title={option.description}
            onClick={() => void commit(index)}
          >
            <span>{option.label}</span>
          </button>
        ))}
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

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
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
    const families = new Map<string, ModelProfile[]>();
    for (const profile of activeGroup.profiles) {
      const family = profile.family || (activeGroup.id === "opencode-go" ? "Other" : "");
      const list = families.get(family) ?? [];
      list.push(profile);
      families.set(family, list);
    }
    const familyEntries = [...families.entries()];
    const needsProviderKey = (
      activeGroup.id === "opencode-go"
      && activeGroup.profiles.every((profile) => profile.configured === false)
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
            <span>{activeGroup.profiles.length} models · choose the exact model for this conversation.</span>
          </div>
        </div>

        {needsProviderKey ? (
          <div className="model-provider-connect">
            <div className="model-provider-connect-copy">
              <KeyRound size={16} />
              <div>
                <strong>Connect OpenCode Go</strong>
                <span>One subscription key unlocks this whole model group. It is stored in your OS credential store.</span>
              </div>
            </div>
            <div className="model-provider-connect-form">
              <input
                type="password"
                value={providerKey}
                onChange={(event) => setProviderKey(event.target.value)}
                placeholder="OpenCode Go subscription key"
                autoComplete="off"
              />
              <button
                type="button"
                disabled={!providerKey.trim() || providerConfiguring}
                onClick={() => void configureProvider("opencode-go")}
              >
                {providerConfiguring ? <RefreshCw size={13} className="model-spin" /> : <KeyRound size={13} />}
                Connect
              </button>
            </div>
          </div>
        ) : null}

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
                            <strong>{profile.name}</strong>
                            {badge ? <em>{badge}</em> : null}
                            {profileReasoning ? <em className="model-reasoning-badge">{profileReasoning.label}</em> : null}
                          </span>
                          <span>{profile.model}</span>
                          <small>{profileSubtitle(profile)}</small>
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
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("list"); setError(""); }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><Cpu size={17} /></span>
          <div>
            <strong>Model providers</strong>
            <span>Choose a provider first, then select a model inside that group.</span>
          </div>
        </div>

        <div className="model-profile-list model-group-list">
          {groups.map((group) => {
            const active = group.profiles.some(
              (profile) => profile.selection === currentSelection && profile.model === currentModel,
            );
            const connected = (
              group.id !== "opencode-go"
              || group.profiles.some((profile) => profile.configured !== false)
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
                      <strong>{group.name}</strong>
                      {active ? <em>Current</em> : null}
                    </span>
                    <span>{group.profiles.length} {group.profiles.length === 1 ? "model" : "models"}</span>
                    <small>
                      {group.id === "opencode-go"
                        ? (connected ? "OpenCode Go subscription · connected" : "OpenCode Go subscription · key required")
                        : group.profiles[0]?.kind === "saved"
                          ? profileSubtitle(group.profiles[0])
                          : "Built-in provider group"}
                    </small>
                  </span>
                  <span className="model-profile-action"><ChevronRight size={14} /></span>
                </button>
              </div>
            );
          })}
        </div>

        <button type="button" className="model-wide-action" onClick={() => { setView("custom"); setError(""); }} disabled={locked}>
          <SlidersHorizontal size={15} />
          <span><strong>Other model ID</strong><small>Same API connection</small></span>
          <ChevronRight size={14} />
        </button>
        {error ? <div className="composer-popover-error">{error}</div> : null}
      </div>
    );
  }

  return (
    <div className="model-manager-view model-manager-home">
      <section className={`model-control-card ${locked ? "locked" : ""}`} aria-label={`Current model ${currentModel}`}>
        <div className="model-control-identity">
          <span className="model-control-icon"><Cpu size={17} strokeWidth={1.8} /></span>
          <div className="model-control-copy" key={`${currentSelection}:${currentModel}`}>
            <span className="model-control-kicker">Current model</span>
            <strong>{currentModel}</strong>
            <small>{currentName} · {adapterLabel(currentAdapter)}</small>
          </div>
          <span className="model-control-active" title="Active model" aria-label="Active model"><Check size={13} strokeWidth={2.1} /></span>
        </div>

        {currentReasoning ? (
          <ReasoningControl
            reasoning={currentReasoning}
            busy={busy}
            running={running}
            onChange={onReasoningChange}
          />
        ) : (
          <div className="model-control-capability-note">
            <span>Model controls</span>
            <small>No reasoning control exposed by this provider.</small>
          </div>
        )}

        <div className="model-control-actions">
          <button type="button" onClick={() => { setView("profiles"); setError(""); }} disabled={locked}>
            <span className="model-control-action-icon"><Cpu size={14.5} /></span>
            <span className="model-control-action-copy"><strong>Models</strong><small>Switch model</small></span>
            <ChevronRight size={13.5} />
          </button>
          <button type="button" onClick={() => { setView("add"); setError(""); }} disabled={locked}>
            <span className="model-control-action-icon"><Plus size={14.5} /></span>
            <span className="model-control-action-copy"><strong>Add model</strong><small>API / endpoint</small></span>
            <ChevronRight size={13.5} />
          </button>
        </div>
      </section>

      {error ? <div className="composer-popover-error">{error}</div> : null}
    </div>
  );
}
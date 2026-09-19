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

type ModelView = "list" | "profiles" | "add" | "custom";

interface ModelPanelProps {
  runtimeModel?: string;
  snapshot: ModelSnapshot | null;
  busy?: boolean;
  running?: boolean;
  onSwitchProfile(selection: string): Promise<void> | void;
  onSwitchCurrent(model: string): Promise<void> | void;
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
  return adapter === "openai" ? "OpenAI" : "OpenAI-compatible";
}

function profileSubtitle(profile: ModelProfile): string {
  if (profile.kind === "builtin") {
    if (profile.baseUrl.includes("relay.smirel.com")) return "Built-in managed · Smirel Relay";
    return `Built-in · ${endpointLabel(profile.baseUrl)}`;
  }
  return `${adapterLabel(profile.adapter)} · ${endpointLabel(profile.baseUrl)}`;
}

function builtinBadge(profile: ModelProfile): string | null {
  if (profile.kind !== "builtin") return null;
  if (profile.selection === "builtin:minimax") return "Primary";
  if (profile.selection.startsWith("builtin:deepseek")) return "DeepSeek";
  return "Managed";
}

function activeReasoningOption(reasoning: ModelReasoningState): ModelReasoningOption | undefined {
  return reasoning.options.find((option) => option.value === reasoning.value);
}

function ReasoningControl({
  modelName,
  reasoning,
  busy,
  running,
  onChange,
}: {
  modelName: string;
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
  const denominator = Math.max(1, reasoning.options.length - 1);
  const progress = reasoning.options.length <= 1 ? 0 : displayIndex / denominator;
  const progressPercent = Math.max(0, Math.min(100, progress * 100));
  const sliderStyle = {
    "--reasoning-progress": `${progressPercent}%`,
    "--reasoning-unfilled": `${100 - progressPercent}%`,
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
    <section className={`reasoning-card ${locked ? "locked" : ""}`} aria-label="Reasoning strength">
      <div className="reasoning-head">
        <span className="reasoning-icon"><BrainCircuit size={16} strokeWidth={1.85} /></span>
        <div className="reasoning-heading-copy">
          <span className="reasoning-kicker">Reasoning effort</span>
          <strong>{modelName}</strong>
        </div>
        <div className="reasoning-head-actions">
          <span className="reasoning-value">{displayOption?.label || reasoning.value}</span>
          <button
            type="button"
            className="reasoning-reset"
            disabled={locked || !canReset}
            onClick={() => void reset()}
            title="Reset reasoning"
            aria-label="Reset reasoning"
          >
            <RotateCcw size={14} />
          </button>
        </div>
      </div>

      <div className="reasoning-detail">
        {displayOption?.description || "Choose how much reasoning time Loom should spend before answering."}
      </div>

      <div className="reasoning-slider-shell" style={sliderStyle}>
        <div className="reasoning-track-base" aria-hidden="true" />
        <div className="reasoning-energy-track" aria-hidden="true" />
        <div className="reasoning-step-points" aria-hidden="true">
          {reasoning.options.map((option, index) => (
            <span
              key={option.value}
              className={`reasoning-step-point ${index <= displayIndex ? "active" : ""} ${index === displayIndex ? "current" : ""}`}
              style={{ left: `${reasoning.options.length <= 1 ? 50 : (index / denominator) * 100}%` }}
            />
          ))}
        </div>
        <input
          className="reasoning-range"
          type="range"
          min={0}
          max={Math.max(0, reasoning.options.length - 1)}
          step={1}
          value={displayIndex}
          disabled={locked || reasoning.options.length <= 1}
          aria-label="Reasoning strength"
          aria-valuetext={displayOption?.label || reasoning.value}
          title={displayOption?.description || "Reasoning strength"}
          onChange={(event) => setDisplayIndex(Number(event.currentTarget.value))}
          onPointerUp={(event) => void commit(Number(event.currentTarget.value))}
          onKeyUp={(event) => {
            if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
              void commit(Number(event.currentTarget.value));
            }
          }}
        />
        <span className="reasoning-thumb" aria-hidden="true"><span /></span>
      </div>

      <div className="reasoning-scale" aria-hidden="true">
        <span>Faster</span>
        <span>Deeper reasoning</span>
      </div>

      {running ? <div className="reasoning-locked-note">Stop the active turn to change reasoning.</div> : null}
      {error ? <div className="composer-popover-error">{error}</div> : null}
    </section>
  );
}

export function ModelPanel({
  runtimeModel,
  snapshot,
  busy,
  running,
  onSwitchProfile,
  onSwitchCurrent,
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

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
  const profiles = snapshot?.profiles ?? [];
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

  if (view === "profiles") {
    return (
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("list"); setError(""); }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><Cpu size={17} /></span>
          <div>
            <strong>Models</strong>
            <span>Choose a saved connection or use another model ID.</span>
          </div>
        </div>

        <div className="model-profile-list">
          {profiles.map((profile) => {
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
                  disabled={locked || exactActive || deleting}
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

        <button type="button" className="model-wide-action" onClick={() => { setView("custom"); setError(""); }} disabled={locked}>
          <SlidersHorizontal size={15} />
          <span><strong>Other model ID</strong><small>Same API connection</small></span>
          <ChevronRight size={14} />
        </button>
        {confirmDelete ? <div className="model-delete-note">Click the trash icon again to delete this saved API connection.</div> : null}
        {error ? <div className="composer-popover-error">{error}</div> : null}
      </div>
    );
  }

  return (
    <div className="model-manager-view model-manager-home">
      {currentReasoning ? (
        <ReasoningControl
          modelName={currentModel}
          reasoning={currentReasoning}
          busy={busy}
          running={running}
          onChange={onReasoningChange}
        />
      ) : (
        <div className="model-compact-current">
          <span className="model-compact-icon"><Cpu size={16} /></span>
          <div><strong>{currentModel}</strong><span>{currentName} · {adapterLabel(currentAdapter)}</span></div>
          <Check size={14} />
        </div>
      )}

      <div className="model-manager-actions">
        <button type="button" onClick={() => { setView("profiles"); setError(""); }} disabled={locked}>
          <span className="model-manager-action-icon"><Cpu size={15} /></span>
          <span><strong>Models</strong><small>{currentModel}</small></span>
          <ChevronRight className="model-manager-action-chevron" size={14} />
        </button>
        <button type="button" onClick={() => { setView("add"); setError(""); }} disabled={locked}>
          <span className="model-manager-action-icon"><Plus size={15} /></span>
          <span><strong>Add API / model</strong><small>Custom endpoint + key</small></span>
          <ChevronRight className="model-manager-action-chevron" size={14} />
        </button>
      </div>

      {error ? <div className="composer-popover-error">{error}</div> : null}
    </div>
  );
}
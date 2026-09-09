import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Gauge,
  Globe,
  KeyRound,
  Plus,
  RefreshCw,
  Server,
  SlidersHorizontal,
  Sparkles,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import type {
  AddModelInput,
  ModelProfile,
  ModelReasoningOption,
  ModelReasoningState,
  ModelSnapshot,
} from "../types/loom";
import "./model-panel.css";

type ModelView = "list" | "add" | "custom";

interface ModelPanelProps {
  runtimeModel?: string;
  snapshot: ModelSnapshot | null;
  busy?: boolean;
  running?: boolean;
  onSwitchProfile(selection: string): Promise<void> | void;
  onSwitchCurrent(model: string): Promise<void> | void;
  onAddModel(input: AddModelInput): Promise<void> | void;
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
  if (profile.kind === "builtin") return "Built-in primary · MiniMax API";
  return `${adapterLabel(profile.adapter)} · ${endpointLabel(profile.baseUrl)}`;
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
  const current = activeReasoningOption(reasoning) ?? reasoning.options[0];
  const currentIsAdvanced = Boolean(current?.advanced);
  const [showAdvanced, setShowAdvanced] = useState(currentIsAdvanced);
  const [error, setError] = useState("");
  const standard = reasoning.options.filter((option) => !option.advanced);
  const advanced = reasoning.options.filter((option) => option.advanced);
  const visible = showAdvanced ? reasoning.options : standard;
  const selectedIndex = Math.max(0, visible.findIndex((option) => option.value === reasoning.value));
  const progress = visible.length <= 1 ? 0 : (selectedIndex / (visible.length - 1)) * 100;
  const locked = Boolean(busy || running);

  async function choose(option: ModelReasoningOption) {
    if (locked || option.value === reasoning.value) return;
    setError("");
    try {
      await onChange(reasoning.kind, option.value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  return (
    <section className="reasoning-card" aria-label="Reasoning strength">
      <div className="reasoning-head">
        <span className="reasoning-icon"><Zap size={16} fill="currentColor" /></span>
        <div className="reasoning-heading-copy">
          <span className="reasoning-eyebrow">Reasoning</span>
          <strong>{current?.label || reasoning.value}</strong>
        </div>
        <span className="reasoning-source">{reasoning.source}</span>
      </div>

      <p className="reasoning-description">
        {current?.description || "Controls how much deliberate reasoning the model uses for new work."}
      </p>

      <div className={`reasoning-scale ${locked ? "locked" : ""}`} style={{ "--reasoning-progress": `${progress}%` } as React.CSSProperties}>
        <div className="reasoning-track" aria-hidden="true"><span /></div>
        <div className="reasoning-stops">
          {visible.map((option) => {
            const selected = option.value === reasoning.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`reasoning-stop ${selected ? "active" : ""} ${option.advanced ? "advanced" : ""}`}
                disabled={locked}
                aria-pressed={selected}
                title={option.description}
                onClick={() => void choose(option)}
              >
                <span className="reasoning-dot">{selected ? <Sparkles size={10} /> : null}</span>
                <span className="reasoning-stop-label">{option.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="reasoning-meta-row">
        <span><Gauge size={12} /> Applies to every model step in the next turn</span>
        {advanced.length ? (
          <button
            type="button"
            className={`reasoning-more ${showAdvanced ? "open" : ""}`}
            disabled={locked && !showAdvanced}
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? "Standard levels" : "More reasoning"}
            <ChevronDown size={12} />
          </button>
        ) : null}
      </div>

      {showAdvanced && advanced.length ? (
        <div className="reasoning-warning">
          <Zap size={12} />
          <span>Max and Ultra are intentionally separated because they can use substantially more reasoning and may change task delegation behavior.</span>
        </div>
      ) : null}
      {running ? <div className="reasoning-locked-note">Stop the active turn before changing reasoning.</div> : null}
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

  const currentModel = snapshot?.current?.model || runtimeModel || "MiniMax-M3";
  const currentName = snapshot?.current?.name || (currentModel.toLowerCase().includes("minimax") ? "MiniMax" : "Current API");
  const currentAdapter = snapshot?.current?.adapter || "openai-compatible";
  const currentBaseUrl = snapshot?.current?.baseUrl || "";
  const currentSelection = snapshot?.current?.selection || "";
  const currentReasoning = snapshot?.current?.reasoning ?? null;
  const profiles = snapshot?.profiles ?? [];
  const savedCount = profiles.filter((profile) => profile.kind === "saved").length;
  const recent = useMemo(
    () => (snapshot?.recentModels ?? []).filter((item) => item && item !== currentModel).slice(0, 4),
    [currentModel, snapshot?.recentModels],
  );
  const locked = Boolean(busy || running);

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

  if (view === "add") {
    return (
      <div className="model-manager-view">
        <button type="button" className="model-back" onClick={() => { setView("list"); setError(""); }}>
          <ArrowLeft size={14} /> Back to models
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><Plus size={17} /></span>
          <div>
            <strong>Add API / model</strong>
            <span>Connect OpenAI directly or any OpenAI-compatible Chat Completions endpoint.</span>
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
              <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Stored in the operating-system credential store" />
            </div>
          </label>
        </div>

        <div className="model-security-note">
          <KeyRound size={13} />
          <span>The key is saved to the OS credential store; Loom only writes non-secret connection metadata to models.json.</span>
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
        <button type="button" className="model-back" onClick={() => { setView("list"); setError(""); }}>
          <ArrowLeft size={14} /> Back to models
        </button>
        <div className="model-form-heading">
          <span className="model-form-icon"><SlidersHorizontal size={17} /></span>
          <div>
            <strong>Use another model ID</strong>
            <span>Keep the current API connection and switch only the provider model name.</span>
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
          <button type="button" className="model-secondary-button" onClick={() => setView("list")}>Cancel</button>
          <button type="button" className="model-primary-button" disabled={locked} onClick={() => void submitCustom()}>
            {busy ? <RefreshCw size={14} className="model-spin" /> : <RefreshCw size={14} />}
            Switch model
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="model-manager-view">
      <div className="model-current-card">
        <div className="model-current-icon"><Cpu size={18} /></div>
        <div className="model-current-copy">
          <div className="model-current-title-row">
            <strong>{currentModel}</strong>
            <span className="model-active-pill"><i /> Active</span>
          </div>
          <span>{currentName} · {adapterLabel(currentAdapter)}</span>
          <small><Globe size={11} /> {endpointLabel(currentBaseUrl || snapshot?.primary.baseUrl || "")}</small>
        </div>
        <span className="model-runtime-check"><Check size={14} /></span>
      </div>

      {currentReasoning ? (
        <ReasoningControl
          reasoning={currentReasoning}
          busy={busy}
          running={running}
          onChange={onReasoningChange}
        />
      ) : (
        <div className="reasoning-unavailable">
          <Gauge size={14} />
          <div><strong>Provider reasoning</strong><span>This model does not advertise a safe reasoning control. Its provider default is used.</span></div>
        </div>
      )}

      <div className="model-section-heading">
        <span>Connections</span>
        <em>{savedCount ? `${savedCount} saved` : "Primary + custom"}</em>
      </div>

      <div className="model-profile-list">
        {profiles.map((profile) => {
          const exactActive = profile.selection === currentSelection && profile.model === currentModel;
          const profileReasoning = profile.reasoning ? activeReasoningOption(profile.reasoning) : null;
          return (
            <button
              key={profile.selection}
              type="button"
              className={`model-profile-row ${exactActive ? "active" : ""}`}
              disabled={locked || exactActive}
              onClick={() => void run(() => onSwitchProfile(profile.selection))}
            >
              <span className={`model-profile-icon ${profile.kind}`}><Server size={15} /></span>
              <span className="model-profile-copy">
                <span className="model-profile-title-row">
                  <strong>{profile.name}</strong>
                  {profile.kind === "builtin" ? <em>Primary</em> : null}
                  {profileReasoning ? <em className="model-reasoning-badge">{profileReasoning.label}</em> : null}
                </span>
                <span>{profile.model}</span>
                <small>{profileSubtitle(profile)}</small>
              </span>
              <span className="model-profile-action">
                {exactActive ? <Check size={14} /> : busy ? <RefreshCw size={13} className="model-spin" /> : <ChevronRight size={14} />}
              </span>
            </button>
          );
        })}
      </div>

      <div className="model-manager-actions">
        <button type="button" onClick={() => { setView("custom"); setError(""); }} disabled={locked}>
          <SlidersHorizontal size={15} />
          <span><strong>Other model ID</strong><small>Same API connection</small></span>
          <ChevronRight size={14} />
        </button>
        <button type="button" onClick={() => { setView("add"); setError(""); }} disabled={locked}>
          <Plus size={15} />
          <span><strong>Add API / model</strong><small>Custom endpoint + key</small></span>
          <ChevronRight size={14} />
        </button>
      </div>

      {error ? <div className="composer-popover-error">{error}</div> : null}
      <div className="model-restart-note">
        <RefreshCw size={12} />
        <span>Model switches restart the local App Server. Reasoning changes apply live to the next turn.</span>
      </div>
    </div>
  );
}

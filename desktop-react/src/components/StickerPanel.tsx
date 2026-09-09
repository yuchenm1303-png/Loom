import { Check, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import type { StickerPreferences } from "../types/loom";
import "./stickers.css";

const DEFAULT_PREFERENCES: StickerPreferences = {
  schema: "ai_ledger_chat_expression_preferences_v2",
  frequency: 50,
  intensity: 50,
  maxPerReply: 0,
  repeatCount: 1,
};

function normalize(preferences?: StickerPreferences | null): StickerPreferences {
  const source = preferences ?? DEFAULT_PREFERENCES;
  return {
    schema: "ai_ledger_chat_expression_preferences_v2",
    frequency: Math.max(0, Math.min(100, Math.round(Number(source.frequency) || 0))),
    intensity: Math.max(0, Math.min(100, Math.round(Number(source.intensity) || 0))),
    maxPerReply: Math.max(0, Math.min(64, Math.round(Number(source.maxPerReply) || 0))),
    repeatCount: Math.max(1, Math.min(4, Math.round(Number(source.repeatCount) || 1))),
  };
}

function samePreferences(a: StickerPreferences, b: StickerPreferences): boolean {
  return a.frequency === b.frequency
    && a.intensity === b.intensity
    && a.maxPerReply === b.maxPerReply
    && a.repeatCount === b.repeatCount;
}

interface RangeControlProps {
  label: string;
  description: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
  displayValue?: string;
  onChange(value: number): void;
}

function RangeControl({
  label,
  description,
  value,
  min,
  max,
  step = 1,
  disabled,
  displayValue,
  onChange,
}: RangeControlProps) {
  const progress = ((value - min) / Math.max(1, max - min)) * 100;
  return (
    <label className="sticker-setting">
      <span className="sticker-setting-head">
        <span>
          <strong>{label}</strong>
          <small>{description}</small>
        </span>
        <em>{displayValue ?? value}</em>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        style={{ "--sticker-progress": `${progress}%` } as CSSProperties}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

interface StickerPanelProps {
  preferences?: StickerPreferences | null;
  disabled?: boolean;
  onSave(preferences: StickerPreferences): Promise<void> | void;
}

export function StickerPanel({ preferences, disabled, onSave }: StickerPanelProps) {
  const canonical = useMemo(() => normalize(preferences), [preferences]);
  const [draft, setDraft] = useState<StickerPreferences>(canonical);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!saving) setDraft(canonical);
  }, [canonical, saving]);

  const dirty = !samePreferences(draft, canonical);
  const locked = Boolean(disabled || saving);

  function update<K extends keyof StickerPreferences>(key: K, value: StickerPreferences[K]) {
    setSaved(false);
    setError("");
    setDraft((current) => normalize({ ...current, [key]: value }));
  }

  async function save() {
    if (!dirty || locked) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await onSave(normalize(draft));
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save sticker preferences.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="sticker-panel">
      <div className="sticker-panel-summary">
        <span className="sticker-panel-symbol" aria-hidden="true"><Sparkles size={16} /></span>
        <div>
          <strong>Chat expression</strong>
          <span>Controls the original AI Ledger inline sticker protocol.</span>
        </div>
      </div>

      <div className="sticker-settings-grid">
        <RangeControl
          label="Frequency"
          description="How often natural reply points can carry a sticker. 0 turns stickers off."
          value={draft.frequency}
          min={0}
          max={100}
          disabled={locked}
          displayValue={draft.frequency === 0 ? "Off" : `${draft.frequency}%`}
          onChange={(value) => update("frequency", value)}
        />
        <RangeControl
          label="Diversity"
          description="How strongly the backend rotates through the sticker catalog."
          value={draft.intensity}
          min={0}
          max={100}
          disabled={locked}
          displayValue={`${draft.intensity}%`}
          onChange={(value) => update("intensity", value)}
        />
        <RangeControl
          label="Max / reply"
          description="Upper limit for one answer. 0 keeps the protocol's automatic limit."
          value={draft.maxPerReply}
          min={0}
          max={64}
          disabled={locked}
          displayValue={draft.maxPerReply === 0 ? "Auto" : String(draft.maxPerReply)}
          onChange={(value) => update("maxPerReply", value)}
        />
        <RangeControl
          label="Repeat"
          description="How many copies are emitted at each chosen expression point."
          value={draft.repeatCount}
          min={1}
          max={4}
          disabled={locked}
          displayValue={`${draft.repeatCount}×`}
          onChange={(value) => update("repeatCount", value)}
        />
      </div>

      {error ? <div className="sticker-panel-error">{error}</div> : null}
      <div className="sticker-panel-actions">
        <button
          type="button"
          className="sticker-reset-button"
          disabled={locked || !dirty}
          onClick={() => {
            setDraft(canonical);
            setSaved(false);
            setError("");
          }}
        >
          <RotateCcw size={13} /> Reset
        </button>
        <button
          type="button"
          className={`sticker-save-button ${saved ? "saved" : ""}`}
          disabled={locked || !dirty}
          onClick={() => void save()}
        >
          {saving ? <span className="composer-mini-spinner" /> : saved ? <Check size={13} /> : null}
          {saving ? "Saving…" : saved ? "Saved" : "Apply"}
        </button>
      </div>
      {disabled ? <div className="sticker-panel-note">Finish or stop the active turn before changing expression settings.</div> : null}
    </div>
  );
}

import { Lock, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { ModelReasoningState } from "../types/loom";
import "./reasoning-thread.css";

interface ReasoningThreadProps {
  reasoning: ModelReasoningState | null;
  busy?: boolean;
  running?: boolean;
  onChange(kind: string, value: string): Promise<void> | void;
}

/**
 * The reasoning control on the model popover's home layer: one thread that is
 * woven up to the selected level, with a bead marking the choice.
 */
export function ReasoningThread({ reasoning, busy, running, onChange }: ReasoningThreadProps) {
  if (!reasoning?.options.length) return <ManagedReasoning />;
  // A different option set (another model) starts from a fresh rail instead of
  // animating the bead across unrelated stops.
  const key = `${reasoning.kind}:${reasoning.options.map((option) => option.value).join(",")}`;
  return <ReasoningRail key={key} reasoning={reasoning} busy={busy} running={running} onChange={onChange} />;
}

function ReasoningRail({ reasoning, busy, running, onChange }: ReasoningThreadProps & { reasoning: ModelReasoningState }) {
  const options = reasoning.options;
  const count = options.length;
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === reasoning.value));
  const [displayIndex, setDisplayIndex] = useState(selectedIndex);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  // Bumped on every change the user makes; it restarts the surge along the
  // thread and plays the burst on the bead.
  const [pulse, setPulse] = useState(0);
  const [error, setError] = useState("");
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const locked = Boolean(busy || running);
  const defaultOption = options.find((option) => option.value === reasoning.defaultValue);
  const canReset = Boolean(defaultOption) && reasoning.value !== reasoning.defaultValue;
  const described = options[previewIndex ?? displayIndex] ?? options[0];
  const previewing = previewIndex !== null && previewIndex !== displayIndex && !locked;

  useEffect(() => {
    setDisplayIndex(selectedIndex);
  }, [selectedIndex]);

  // 0 at the first level and 1 at the last. The stylesheet derives the bead
  // position, the woven length, the lit stops and the colour from it.
  const levelAt = (index: number) => (count > 1 ? index / (count - 1) : 0);
  const railStyle = {
    "--rt-count": String(count),
    "--rt-t": String(levelAt(displayIndex)),
  } as CSSProperties;
  // At rest the preview bead hides inside the real one, so it glides out
  // toward the hovered level and back.
  const ghostStyle = {
    "--rt-ghost-t": String(levelAt(previewing ? previewIndex : displayIndex)),
  } as CSSProperties;
  // The first level needs a hint of the woven treatment even when the active
  // span itself has zero width. Keep that accent local to the first stop: the
  // previous full-width lead made the animated braid look detached from the
  // Direct control on sparse (especially two-option) rails.
  const leadStyle = {
    left: "calc(var(--rt-start) - 22px)",
    width: "40px",
    opacity: 0.68,
    WebkitMaskImage: "linear-gradient(90deg, transparent, #000 28%, #000 72%, transparent)",
    maskImage: "linear-gradient(90deg, transparent, #000 28%, #000 72%, transparent)",
  } as CSSProperties;
  const leadFiberStyle = {
    background: "var(--rt-cool)",
  } as CSSProperties;

  async function commit(index: number) {
    const option = options[index];
    if (!option || locked || option.value === reasoning.value) return;
    setError("");
    setDisplayIndex(index);
    setPulse((value) => value + 1);
    try {
      await onChange(reasoning.kind, option.value);
    } catch (cause) {
      setDisplayIndex(selectedIndex);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.key === "Home"
      ? 0
      : event.key === "End"
        ? count - 1
        : event.key === "ArrowRight" || event.key === "ArrowDown"
          ? displayIndex + 1
          : event.key === "ArrowLeft" || event.key === "ArrowUp"
            ? displayIndex - 1
            : -1;
    if (target < 0 || target >= count) return;
    event.preventDefault();
    optionRefs.current[target]?.focus();
    void commit(target);
  }

  return (
    <section
      className={`rt${running ? " is-running" : ""}${busy ? " is-busy" : ""}${count >= 5 ? " is-dense" : ""}`}
      aria-label="Reasoning"
    >
      <div className="rt-head">
        <span className="rt-eyebrow">Reasoning</span>
        {canReset && defaultOption ? (
          <button
            type="button"
            className="rt-reset"
            disabled={locked}
            title={`Reset to ${defaultOption.label}`}
            onClick={() => void commit(options.indexOf(defaultOption))}
          >
            <RotateCcw size={12} strokeWidth={2} aria-hidden="true" />
            <span>Reset</span>
          </button>
        ) : null}
      </div>

      <div className="rt-rail" style={railStyle}>
        <span className="rt-track" aria-hidden="true" />
        <span className="rt-fill rt-lead" style={leadStyle} aria-hidden="true">
          <span className="rt-strand is-back" style={leadFiberStyle} />
          <span className="rt-spine" style={leadFiberStyle} />
          <span className="rt-strand is-front" style={leadFiberStyle} />
        </span>
        <span className="rt-fill" aria-hidden="true">
          <span className="rt-strand is-back" />
          <span className="rt-spine" />
          <span className="rt-strand is-front" />
          <span key={pulse} className="rt-comet" />
        </span>
        <div
          className="rt-options"
          role="radiogroup"
          aria-label="Reasoning effort"
          onKeyDown={handleKeyDown}
          onPointerLeave={() => setPreviewIndex(null)}
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={index === displayIndex}
              tabIndex={index === displayIndex ? 0 : -1}
              className={`rt-option${index === displayIndex ? " is-active" : ""}`}
              style={{ "--rt-i": String(levelAt(index)) } as CSSProperties}
              // aria-disabled rather than disabled: a disabled button drops
              // focus mid-change, which would break arrow-key stepping.
              aria-disabled={locked || count < 2 || undefined}
              onPointerEnter={() => setPreviewIndex(index)}
              onFocus={() => setPreviewIndex(index)}
              onBlur={() => setPreviewIndex(null)}
              onClick={() => void commit(index)}
            >
              <i className="rt-stop" aria-hidden="true" />
              <span className="rt-label">{option.label}</span>
            </button>
          ))}
        </div>
        <span className={`rt-ghost${previewing ? " is-on" : ""}`} style={ghostStyle} aria-hidden="true" />
        <span className="rt-bead" aria-hidden="true">
          <span key={pulse} className="rt-aura" />
          {pulse ? (
            <span key={`burst-${pulse}`} className="rt-burst">
              <i />
              <i />
              <i />
              <i />
            </span>
          ) : null}
          <span className="rt-orbit" />
          <span className="rt-core" />
        </span>
      </div>

      {running ? (
        <div className="rt-caption rt-note">
          <Lock size={12} strokeWidth={2} aria-hidden="true" />
          <span>Stop the active turn to change reasoning.</span>
        </div>
      ) : (
        <div key={described.value} className="rt-caption" title={described.description}>
          {described.description}
        </div>
      )}
      {error ? <div className="rt-error" role="alert">{error}</div> : null}
    </section>
  );
}

function ManagedReasoning() {
  return (
    <section className="rt is-managed" aria-label="Reasoning">
      <div className="rt-head">
        <span className="rt-eyebrow">Reasoning</span>
      </div>
      <div className="rt-auto">
        <span className="rt-auto-line" aria-hidden="true" />
        <span className="rt-auto-badge">Auto</span>
        <span className="rt-auto-line" aria-hidden="true" />
      </div>
      <div className="rt-caption">This model decides how deeply to reason.</div>
    </section>
  );
}

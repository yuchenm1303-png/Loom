import { Lock, RotateCcw } from "./icons";
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { ModelReasoningOption, ModelReasoningState } from "../types/loom";
import { useI18n } from "../i18n";
import { PanelLabel } from "./composerPanel";

interface ReasoningControlProps {
  reasoning: ModelReasoningState | null;
  busy?: boolean;
  running?: boolean;
  onChange(kind: string, value: string): Promise<void> | void;
}

// The Host names the levels in English; these are the same words for the Chinese interface.
const ZH_LABELS: Record<string, string> = {
  none: "直接", disabled: "关闭", adaptive: "自适应", enabled: "开启", minimal: "最少", low: "低", medium: "中",
  high: "高", xhigh: "超高", max: "最高", ultra: "极限",
};
const ZH_DESCRIPTIONS: Record<string, string> = {
  none: "不做深度推理（连接支持时）。",
  disabled: "关闭深度思考，延迟最低。",
  adaptive: "由模型判断何时需要更深的推理。",
  minimal: "使用模型最少的推理量。",
  low: "较轻的推理，适合直接的任务。",
  medium: "在延迟与推理深度之间取得平衡，适合日常工作。",
  high: "更深的推理，适合复杂的多步任务。",
  xhigh: "额外的推理深度，适合困难的多步任务。",
  max: "使用模型支持的最高推理设置。",
  ultra: "使用模型最高的扩展推理模式。",
};

function optionText(option: ModelReasoningOption, zh: boolean) {
  return {
    label: zh ? ZH_LABELS[option.value] ?? option.label : option.label,
    description: zh ? ZH_DESCRIPTIONS[option.value] ?? option.description : option.description,
  };
}

/**
 * Reasoning on the model card: a rail with a stop for every level the model offers, filled up to the chosen one. The
 * knob is a mark and travels (the settle spring); the words under the stops only change ink. Hovering a stop reads its
 * description under the rail before you choose it.
 */
export function ReasoningControl({ reasoning, busy, running, onChange }: ReasoningControlProps) {
  const zh = useI18n().language === "zh-CN";
  if (!reasoning?.options.length) {
    return (
      <section className="cp-reasoning is-managed" aria-label={zh ? "推理" : "Reasoning"}>
        <PanelLabel>{zh ? "推理" : "Reasoning"}</PanelLabel>
        <div className="cp-auto">{zh ? "自动" : "Automatic"}</div>
        <div className="cp-caption">{zh ? "由模型自己决定思考的深度。" : "This model decides how deeply to reason."}</div>
      </section>
    );
  }
  // A different set of levels (another model) is a new rail: the knob does not travel across unrelated stops.
  const key = `${reasoning.kind}:${reasoning.options.map((option) => option.value).join(",")}`;
  return <ReasoningSteps key={key} reasoning={reasoning} busy={busy} running={running} onChange={onChange} zh={zh} />;
}

function ReasoningSteps({ reasoning, busy, running, onChange, zh }: ReasoningControlProps & { reasoning: ModelReasoningState; zh: boolean }) {
  const options = reasoning.options;
  const count = options.length;
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === reasoning.value));
  const [displayIndex, setDisplayIndex] = useState(selectedIndex);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const stopRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const locked = Boolean(busy || running);
  const defaultOption = options.find((option) => option.value === reasoning.defaultValue);
  const canReset = Boolean(defaultOption) && reasoning.value !== reasoning.defaultValue;
  const described = options[previewIndex ?? displayIndex] ?? options[0];

  useEffect(() => {
    setDisplayIndex(selectedIndex);
  }, [selectedIndex]);

  async function commit(index: number) {
    const option = options[index];
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

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.key === "Home"
      ? 0
      : event.key === "End"
        ? count - 1
        : event.key === "ArrowRight" || event.key === "ArrowDown"
          ? displayIndex + 1
          : event.key === "ArrowLeft" || event.key === "ArrowUp"
            ? displayIndex - 1
            : -1;
    if (!["Home", "End", "ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    // The rail owns its arrows; the panel's row walk leaves them alone.
    event.preventDefault();
    if (target < 0 || target >= count) return;
    stopRefs.current[target]?.focus();
    void commit(target);
  }

  const style = {
    "--cp-count": String(count),
    "--cp-t": String(count > 1 ? displayIndex / (count - 1) : 0),
  } as CSSProperties;
  const describedText = optionText(described, zh);
  const defaultText = defaultOption ? optionText(defaultOption, zh) : null;

  return (
    <section className={`cp-reasoning${locked ? " is-locked" : ""}`} aria-label={zh ? "推理" : "Reasoning"}>
      <PanelLabel
        action={canReset && defaultText ? (
          <button
            type="button"
            className="cp-link"
            disabled={locked}
            title={zh ? `恢复为 ${defaultText.label}` : `Reset to ${defaultText.label}`}
            onClick={() => void commit(options.indexOf(defaultOption!))}
          >
            <RotateCcw size={12} strokeWidth={2} aria-hidden="true" />
            <span>{zh ? "恢复默认" : "Reset"}</span>
          </button>
        ) : null}
      >
        {zh ? "推理" : "Reasoning"}
      </PanelLabel>

      <div
        className={`cp-steps${count >= 6 ? " is-dense" : ""}`}
        style={style}
        role="radiogroup"
        aria-label={zh ? "推理强度" : "Reasoning effort"}
        onKeyDown={onKeyDown}
        onPointerLeave={() => setPreviewIndex(null)}
      >
        <span className="cp-steps-rail" aria-hidden="true">
          <span className="cp-steps-track" />
          <span className="cp-steps-fill" />
        </span>
        {options.map((option, index) => (
          <button
            key={option.value}
            ref={(node) => {
              stopRefs.current[index] = node;
            }}
            type="button"
            role="radio"
            className={`cp-step${index < displayIndex ? " is-filled" : ""}`}
            aria-checked={index === displayIndex}
            tabIndex={index === displayIndex ? 0 : -1}
            // aria-disabled rather than disabled: a disabled button drops focus mid-change, which would break stepping.
            aria-disabled={locked || count < 2 || undefined}
            onPointerEnter={() => setPreviewIndex(index)}
            onFocus={() => setPreviewIndex(index)}
            onBlur={() => setPreviewIndex(null)}
            onClick={() => void commit(index)}
          >
            <span className="cp-step-label">{optionText(option, zh).label}</span>
          </button>
        ))}
        <span className="cp-steps-knob" aria-hidden="true"><i /></span>
      </div>

      {running ? (
        <div className="cp-caption is-note">
          <Lock size={12} strokeWidth={2} aria-hidden="true" />
          <span>{zh ? "先停止当前任务，再调整推理强度。" : "Stop the active turn to change reasoning."}</span>
        </div>
      ) : (
        <div key={described.value} className="cp-caption" title={describedText.description}>
          {describedText.description}
        </div>
      )}
      {error ? <div className="cp-caption is-error" role="alert">{error}</div> : null}
    </section>
  );
}

import { Lock, RotateCcw } from "./icons";
import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
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

// The scale, in CSS px: how tall the first tick and the last are, how the ramp between them bends (1 is straight; a
// little more keeps the low levels low), how much longer a level's tick is, and how far the needle stands above it.
const RAMP = { first: 6, last: 24, bow: 1.25, major: 3, needle: 10 };
// The needle is drawn this tall and scaled down to the ramp, so that it moves with `transform` only.
const NEEDLE_MAX = RAMP.last + RAMP.major + RAMP.needle;
// About this many ticks run from the first level to the last whatever the number of levels: one every ~7px.
const TICKS_ACROSS = 50;
// A press that moves less than this is a click; more is a drag.
const DRAG_SLOP = 3;

const rampHeight = (t: number) => RAMP.first + (RAMP.last - RAMP.first) * Math.pow(Math.min(1, Math.max(0, t)), RAMP.bow);
const needleHeight = (t: number) => Math.round(rampHeight(t) + RAMP.major + RAMP.needle);

interface Tick {
  t: number;
  major: boolean;
  level: number;
}

/** One tick per step between the levels and a longer one on each level; t runs 0 to 1 from the first level to the last. */
function scaleTicks(count: number): Tick[] {
  if (count < 2) return [{ t: 0, major: true, level: 0 }];
  const between = Math.min(24, Math.max(4, Math.round(TICKS_ACROSS / count)));
  const total = (count - 1) * between + 1;
  return Array.from({ length: total }, (_, index) => ({ t: index / (total - 1), major: index % between === 0, level: Math.floor(index / between) }));
}

/**
 * One layer of ticks. The layer never changes while the needle travels: the accent copy of it is revealed by a window
 * that moves with the needle, so a level change is a few `transform` transitions on the compositor and no restyling of
 * the ticks (a custom property carrying the needle's place restyled every tick on every frame: ~3 ms a frame here).
 */
const Ticks = memo(function Ticks({ count, peek, size = 1 }: { count: number; peek: number | null; size?: number }) {
  const ticks = useMemo(() => scaleTicks(count), [count]);
  return (
    <span className="cp-ticks">
      {ticks.map((tick, index) => (
        <span
          key={index}
          className={tick.major ? (tick.level === peek ? "is-major is-peek" : "is-major") : undefined}
          style={{ left: `${tick.t * 100}%`, height: Math.round((rampHeight(tick.t) + (tick.major ? RAMP.major : 0)) * size) }}
        />
      ))}
    </span>
  );
});

/**
 * Reasoning on the model card: a scale with a tick for every step between the levels the model offers, taller the
 * deeper the reasoning, lit up to the chosen level, and a needle on it. The needle is the one mark that travels (the
 * settle spring) and the lit ticks are revealed as it passes; the words under the levels only change ink. A press
 * anywhere on the scale chooses the nearest level, a drag carries the needle and chooses where it lets go, and hovering
 * a level reads its description under the scale before you choose it.
 */
export function ReasoningControl({ reasoning, busy, running, onChange }: ReasoningControlProps) {
  const zh = useI18n().language === "zh-CN";
  if (!reasoning?.options.length) {
    return (
      <section className="cp-reasoning is-managed" aria-label={zh ? "推理" : "Reasoning"}>
        <PanelLabel>{zh ? "推理" : "Reasoning"}</PanelLabel>
        <div className="cp-auto">
          <span>{zh ? "自动" : "Automatic"}</span>
          <span className="cp-scale" aria-hidden="true">
            <Ticks count={5} peek={null} size={0.55} />
          </span>
        </div>
        <div className="cp-caption">{zh ? "由模型自己决定思考的深度。" : "This model decides how deeply to reason."}</div>
      </section>
    );
  }
  // A different set of levels (another model) is a new scale: the needle does not travel across unrelated levels.
  const key = `${reasoning.kind}:${reasoning.options.map((option) => option.value).join(",")}`;
  return <ReasoningSteps key={key} reasoning={reasoning} busy={busy} running={running} onChange={onChange} zh={zh} />;
}

interface Gesture {
  pointerId: number;
  x: number;
  dragging: boolean;
}

function ReasoningSteps({ reasoning, busy, running, onChange, zh }: ReasoningControlProps & { reasoning: ModelReasoningState; zh: boolean }) {
  const options = reasoning.options;
  const count = options.length;
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === reasoning.value));
  const [displayIndex, setDisplayIndex] = useState(selectedIndex);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  // While a drag carries the needle: its place in levels (fractions between them), else null.
  const [dragAt, setDragAt] = useState<number | null>(null);
  const [held, setHeld] = useState(false);
  const [error, setError] = useState("");
  const railRef = useRef<HTMLDivElement | null>(null);
  const stopRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const gesture = useRef<Gesture | null>(null);
  const locked = Boolean(busy || running);
  const defaultOption = options.find((option) => option.value === reasoning.defaultValue);
  const canReset = Boolean(defaultOption) && reasoning.value !== reasoning.defaultValue;
  const dragging = dragAt !== null;
  const described = options[previewIndex ?? displayIndex] ?? options[0];

  useEffect(() => {
    setDisplayIndex(selectedIndex);
  }, [selectedIndex]);

  async function commit(index: number) {
    const option = options[index];
    if (!option || locked) return;
    // The needle goes to the level at once; the Host answers after.
    setDisplayIndex(index);
    if (option.value === reasoning.value) return;
    setError("");
    try {
      await onChange(reasoning.kind, option.value);
    } catch (cause) {
      setDisplayIndex(selectedIndex);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  /** Where a pointer is on the scale, in levels: 0 at the first, count - 1 at the last, fractions between. */
  function levelAt(clientX: number) {
    const rail = railRef.current;
    if (!rail || count < 2) return 0;
    const box = rail.getBoundingClientRect();
    const level = ((clientX - box.left) / Math.max(1, box.width)) * count - 0.5;
    return Math.min(count - 1, Math.max(0, level));
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (locked || count < 2 || event.button !== 0) return;
    gesture.current = { pointerId: event.pointerId, x: event.clientX, dragging: false };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // A pointer that cannot be captured still chooses a level on release.
    }
    // A press already answers: the needle starts for the nearest level (a drag takes over from there).
    setHeld(true);
    setDisplayIndex(Math.round(levelAt(event.clientX)));
    setPreviewIndex(null);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const pressed = gesture.current;
    if (pressed && pressed.pointerId === event.pointerId) {
      if (!pressed.dragging && Math.abs(event.clientX - pressed.x) < DRAG_SLOP) return;
      pressed.dragging = true;
      // The needle goes where the pointer is; the level under it is the one that reads as chosen until it lets go.
      const level = levelAt(event.clientX);
      setDragAt(level);
      setDisplayIndex(Math.round(level));
      return;
    }
    if (event.pointerType === "touch") return;
    const index = Math.round(levelAt(event.clientX));
    setPreviewIndex((current) => (current === index ? current : index));
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    const pressed = gesture.current;
    if (!pressed || pressed.pointerId !== event.pointerId) return;
    gesture.current = null;
    setHeld(false);
    setDragAt(null);
    const index = Math.round(levelAt(event.clientX));
    stopRefs.current[index]?.focus({ preventScroll: true });
    void commit(index);
  }

  function onPointerCancel(event: PointerEvent<HTMLDivElement>) {
    if (gesture.current?.pointerId !== event.pointerId) return;
    gesture.current = null;
    setHeld(false);
    setDragAt(null);
    setDisplayIndex(selectedIndex);
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
    // The scale owns its arrows; the panel's row walk leaves them alone.
    event.preventDefault();
    if (target < 0 || target >= count) return;
    stopRefs.current[target]?.focus();
    void commit(target);
  }

  // The needle's place along the scale, 0 to 1, and how tall it stands there.
  const span = Math.max(1, count - 1);
  const place = (dragAt ?? displayIndex) / span;
  const height = needleHeight(place);
  const peek = previewIndex !== null && !dragging && previewIndex !== displayIndex ? previewIndex : null;
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
        ref={railRef}
        className={`cp-steps${count >= 6 ? " is-dense" : ""}${dragging ? " is-dragging" : ""}${held ? " is-held" : ""}`}
        style={{ "--cp-n": count } as CSSProperties}
        role="radiogroup"
        aria-label={zh ? "推理强度" : "Reasoning effort"}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onPointerLeave={() => setPreviewIndex(null)}
      >
        <span className="cp-scale" aria-hidden="true">
          <Ticks count={count} peek={peek} />
          <span className="cp-lit" style={{ transform: `translateX(${(place - 1) * 100}%)` }}>
            <span className="cp-lit-ticks" style={{ transform: `translateX(${(1 - place) * 100}%)` }}>
              <Ticks count={count} peek={peek} />
            </span>
          </span>
          <span className="cp-slide" style={{ transform: `translateX(${place * 100}%)` }}>
            <span className="cp-needle" style={{ height: NEEDLE_MAX, transform: `scaleY(${height / NEEDLE_MAX})` }} />
            <span className="cp-pin" style={{ transform: `translateY(${-height}px)` }}>
              <span className="cp-pin-dot" />
            </span>
          </span>
        </span>
        {options.map((option, index) => (
          <button
            key={option.value}
            ref={(node) => {
              stopRefs.current[index] = node;
            }}
            type="button"
            role="radio"
            className={`cp-step${index === previewIndex && !dragging ? " is-peek" : ""}`}
            aria-checked={index === displayIndex}
            tabIndex={index === displayIndex ? 0 : -1}
            // aria-disabled rather than disabled: a disabled button drops focus mid-change, which would break stepping.
            aria-disabled={locked || count < 2 || undefined}
            onFocus={() => setPreviewIndex(index)}
            onBlur={() => setPreviewIndex(null)}
            // A pointer chooses through the gesture above (the scale holds the pointer, so no click reaches here);
            // what is left is the keyboard and assistive technology, which click with no pointer (detail 0).
            onClick={(event) => {
              if (event.detail === 0) void commit(index);
            }}
          >
            <span className="cp-step-label">{optionText(option, zh).label}</span>
          </button>
        ))}
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

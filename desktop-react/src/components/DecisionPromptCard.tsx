import { Check, MessageSquareText, Send } from "lucide-react";
import { useMemo, useState } from "react";
import { useI18n } from "../i18n";
import "./decision-prompt-card.css";

export interface DecisionPromptOption {
  id: string;
  title: string;
  description?: string;
  recommended?: boolean;
}

export interface DecisionPromptSpec {
  id?: string;
  title: string;
  description?: string;
  options: DecisionPromptOption[];
  multiple?: boolean;
  allowCustomInput: boolean;
  customPlaceholder?: string;
}

export interface ParsedDecisionMessage {
  text: string;
  decisions: DecisionPromptSpec[];
  incomplete: boolean;
}

const DECISION_FENCE = "loom-decision";
const MAX_OPTIONS = 6;

function cleanText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeDecision(value: unknown): DecisionPromptSpec | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  const title = cleanText(payload.title);
  const rawOptions = Array.isArray(payload.options) ? payload.options.slice(0, MAX_OPTIONS) : [];
  if (!title || rawOptions.length < 2) return null;

  const options: DecisionPromptOption[] = [];
  const ids = new Set<string>();
  for (let index = 0; index < rawOptions.length; index += 1) {
    const raw = rawOptions[index];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const option = raw as Record<string, unknown>;
    const id = cleanText(option.id) || String.fromCharCode(65 + index);
    const optionTitle = cleanText(option.title);
    if (!id || !optionTitle || ids.has(id)) return null;
    ids.add(id);
    options.push({
      id,
      title: optionTitle,
      description: cleanText(option.description) || undefined,
      recommended: option.recommended === true,
    });
  }

  return {
    id: cleanText(payload.id) || undefined,
    title,
    description: cleanText(payload.description) || undefined,
    options,
    multiple: payload.multiple === true
      || payload.multiSelect === true
      || cleanText(payload.selectionMode).toLowerCase() === "multiple"
      || cleanText(payload.mode).toLowerCase() === "multiple",
    allowCustomInput: payload.allowCustomInput !== false,
    customPlaceholder: cleanText(payload.customPlaceholder) || undefined,
  };
}

const DECISION_CUE_RE = /(?:请选择(?:一个|方案|选项)?|请选(?:一个|方案|选项)?|choose\s+(?:one|an?\s+option|an?\s+plan)|select\s+(?:one|an?\s+option))\s*[：:]\s*$/i;

export function parseDecisionMessage(content: string, streaming = false): ParsedDecisionMessage {
  const decisions: DecisionPromptSpec[] = [];
  const source = String(content ?? "");
  let incomplete = false;
  // Accept both the documented newline form and compact providers that place
  // the JSON object immediately after the fence language marker.
  const completeFence = /```loom-decision\b[ \t]*(?:\r?\n)?([\s\S]*?)```/gi;

  let text = source.replace(completeFence, (_whole, body: string) => {
    try {
      const decision = normalizeDecision(JSON.parse(body.trim()));
      if (!decision) {
        incomplete = true;
        return "\n";
      }
      decisions.push(decision);
      return "\n";
    } catch {
      incomplete = true;
      return "\n";
    }
  });

  const lowered = text.toLowerCase();
  const marker = `\`\`\`${DECISION_FENCE}`;
  const open = lowered.lastIndexOf(marker);
  if (open >= 0) {
    const close = text.indexOf("```", open + marker.length);
    if (close < 0) {
      // Never flash half-written JSON while the model is streaming. If the
      // item later becomes terminal with the fence still open, surface a
      // recovery card instead of silently swallowing the missing options.
      if (!streaming) incomplete = true;
      text = text.slice(0, open);
    }
  }

  text = text.trim();
  if (!streaming && !decisions.length && DECISION_CUE_RE.test(text)) incomplete = true;
  return { text, decisions, incomplete };
}

function decisionResponse(
  spec: DecisionPromptSpec,
  selected: DecisionPromptOption[],
  note: string,
  zh: boolean,
): string {
  const cleanNote = note.trim();
  const choiceText = selected.map((option) => `${option.id}「${option.title}」`).join("、");
  const choiceTextEn = selected.map((option) => `${option.id}: ${option.title}`).join(", ");

  if (zh) {
    if (selected.length && cleanNote) {
      return `关于“${spec.title}”：我选择 ${choiceText}。补充意见：${cleanNote}`;
    }
    if (selected.length) return `关于“${spec.title}”：我选择 ${choiceText}。`;
    return `关于“${spec.title}”，我的意见是：${cleanNote}`;
  }
  if (selected.length && cleanNote) {
    return `For “${spec.title}”, I choose ${choiceTextEn}. Additional guidance: ${cleanNote}`;
  }
  if (selected.length) return `For “${spec.title}”, I choose ${choiceTextEn}.`;
  return `For “${spec.title}”, my preference is: ${cleanNote}`;
}

export function DecisionPromptRecoveryCard({
  disabled = false,
  onRetry,
}: {
  disabled?: boolean;
  onRetry?(): Promise<void> | void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function retry() {
    if (!onRetry || disabled || sending || sent) return;
    setSending(true);
    setError("");
    try {
      await onRetry();
      setSent(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="decision-card decision-recovery" aria-label={zh ? "选项生成未完成" : "Decision options incomplete"}>
      <div className="decision-recovery-copy">
        <span className="decision-card-icon" aria-hidden="true"><MessageSquareText size={16} /></span>
        <div>
          <strong>{zh ? "选项生成未完成" : "Decision options were incomplete"}</strong>
          <p>{zh ? "上一条回复在选项生成完成前结束了，可以只重新生成选项。" : "The reply ended before the options finished. Regenerate only the decision choices."}</p>
        </div>
      </div>
      {error ? <span className="decision-error">{error}</span> : null}
      <button
        type="button"
        className="decision-submit"
        disabled={!onRetry || disabled || sending || sent}
        onClick={() => void retry()}
      >
        {sent ? <Check size={13} /> : <Send size={13} />}
        <span>{sent ? (zh ? "已请求重新生成" : "Requested") : sending ? (zh ? "正在发送…" : "Sending…") : (zh ? "重新生成选项" : "Regenerate options")}</span>
      </button>
    </section>
  );
}

export function DecisionPromptCard({
  spec,
  disabled = false,
  onSubmit,
}: {
  spec: DecisionPromptSpec;
  disabled?: boolean;
  onSubmit?(response: string): Promise<void> | void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [customOpen, setCustomOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState<DecisionPromptOption[] | null | undefined>(undefined);
  const [error, setError] = useState("");

  const selected = useMemo(
    () => spec.options.filter((option) => selectedIds.includes(option.id)),
    [selectedIds, spec.options],
  );
  const canSubmit = Boolean(onSubmit && !disabled && !sending && (selected.length || note.trim()));

  async function submit() {
    if (!canSubmit || !onSubmit) return;
    setSending(true);
    setError("");
    try {
      await onSubmit(decisionResponse(spec, selected, note, zh));
      setSubmitted(selected.length ? selected : null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  }

  const locked = disabled || submitted !== undefined;

  return (
    <section className={`decision-card ${spec.multiple ? "is-multiple" : "is-single"} ${submitted !== undefined ? "is-submitted" : ""}`} aria-label={spec.title}>
      <header className="decision-card-head">
        <span className="decision-card-icon" aria-hidden="true"><MessageSquareText size={16} /></span>
        <div className="decision-card-heading">
          <div className="decision-card-title-row">
            <strong>{spec.title}</strong>
            {spec.multiple ? <span className="decision-mode-badge">{zh ? "可多选" : "Multi-select"}</span> : null}
          </div>
          {spec.description ? <p>{spec.description}</p> : null}
        </div>
      </header>

      <div
        className="decision-options"
        role={spec.multiple ? "group" : "radiogroup"}
        aria-label={spec.title}
        aria-multiselectable={spec.multiple || undefined}
      >
        {spec.options.map((option) => {
          const active = selectedIds.includes(option.id);
          return (
            <button
              key={option.id}
              type="button"
              className={`decision-option ${active ? "is-selected" : ""}`}
              role={spec.multiple ? "checkbox" : "radio"}
              aria-checked={active}
              disabled={locked}
              onClick={() => {
                setSelectedIds((current) => {
                  if (!spec.multiple) return [option.id];
                  return current.includes(option.id)
                    ? current.filter((id) => id !== option.id)
                    : [...current, option.id];
                });
                setError("");
              }}
            >
              <span className="decision-option-marker" aria-hidden="true">
                <span className="decision-option-marker-core">
                  {active ? <Check size={12} strokeWidth={2.2} /> : null}
                </span>
              </span>
              <span className="decision-option-copy">
                <span className="decision-option-title-row">
                  <strong>{option.title}</strong>
                  {option.recommended ? <em>{zh ? "建议" : "Suggested"}</em> : null}
                </span>
                {option.description ? <span>{option.description}</span> : null}
              </span>
            </button>
          );
        })}
      </div>

      {submitted !== undefined ? (
        <div className="decision-submitted" role="status">
          <Check size={14} />
          <span>
            {submitted?.length
              ? (zh
                ? `已选择 ${submitted.map((option) => option.title).join("、")}`
                : `Selected ${submitted.map((option) => option.title).join(", ")}`)
              : (zh ? "已发送你的意见" : "Your preference was sent")}
          </span>
        </div>
      ) : (
        <div className="decision-card-foot">
          {spec.allowCustomInput ? (
            <div className={`decision-custom ${customOpen ? "is-open" : ""}`}>
              <button
                type="button"
                className="decision-custom-toggle"
                disabled={disabled || sending}
                onClick={() => setCustomOpen((current) => !current)}
              >
                <MessageSquareText size={13} />
                <span>{zh ? "补充或提出自己的意见" : "Add or propose your own preference"}</span>
              </button>
              {customOpen ? (
                <textarea
                  value={note}
                  onChange={(event) => {
                    setNote(event.target.value);
                    setError("");
                  }}
                  placeholder={spec.customPlaceholder || (zh ? "例如：选 A，但不要包含之前的未提交改动…" : "For example: choose A, but leave the earlier uncommitted changes out…")}
                  disabled={disabled || sending}
                  rows={2}
                />
              ) : null}
            </div>
          ) : null}

          <div className="decision-actions">
            {error ? (
              <span className="decision-error">{error}</span>
            ) : (
              <span className="decision-hint">
                {spec.multiple
                  ? (zh ? "可以选择多个方案，再一起确认。" : "Select one or more options, then confirm together.")
                  : (zh ? "选择一个方案，也可以补充条件。" : "Choose an option and optionally add guidance.")}
              </span>
            )}
            <button
              type="button"
              className="decision-submit"
              disabled={!canSubmit}
              onClick={() => void submit()}
            >
              <span>{sending
                ? (zh ? "正在发送…" : "Sending…")
                : selected.length
                  ? (spec.multiple
                    ? (zh ? `确认 ${selected.length} 项` : `Confirm ${selected.length}`)
                    : (zh ? "确认选择" : "Confirm choice"))
                  : (zh ? "发送意见" : "Send preference")}</span>
              <Send size={13} />
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

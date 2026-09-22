import { Check, ChevronRight, MessageSquareText, Send } from "lucide-react";
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
  allowCustomInput: boolean;
  customPlaceholder?: string;
}

export interface ParsedDecisionMessage {
  text: string;
  decisions: DecisionPromptSpec[];
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
    allowCustomInput: payload.allowCustomInput !== false,
    customPlaceholder: cleanText(payload.customPlaceholder) || undefined,
  };
}

export function parseDecisionMessage(content: string): ParsedDecisionMessage {
  const decisions: DecisionPromptSpec[] = [];
  const source = String(content ?? "");
  const completeFence = /```loom-decision[ \t]*\r?\n([\s\S]*?)```/gi;

  let text = source.replace(completeFence, (whole, body: string) => {
    try {
      const decision = normalizeDecision(JSON.parse(body));
      if (!decision) return whole;
      decisions.push(decision);
      return "\n";
    } catch {
      return whole;
    }
  });

  const lowered = text.toLowerCase();
  const marker = `\`\`\`${DECISION_FENCE}`;
  const open = lowered.lastIndexOf(marker);
  if (open >= 0) {
    const close = text.indexOf("```", open + marker.length);
    if (close < 0) text = text.slice(0, open);
  }

  return { text: text.trim(), decisions };
}

function decisionResponse(
  spec: DecisionPromptSpec,
  selected: DecisionPromptOption | null,
  note: string,
  zh: boolean,
): string {
  const cleanNote = note.trim();
  if (zh) {
    if (selected && cleanNote) {
      return `关于“${spec.title}”：我选择 ${selected.id}「${selected.title}」。补充意见：${cleanNote}`;
    }
    if (selected) return `关于“${spec.title}”：我选择 ${selected.id}「${selected.title}」。`;
    return `关于“${spec.title}”，我的意见是：${cleanNote}`;
  }
  if (selected && cleanNote) {
    return `For “${spec.title}”, I choose ${selected.id}: ${selected.title}. Additional guidance: ${cleanNote}`;
  }
  if (selected) return `For “${spec.title}”, I choose ${selected.id}: ${selected.title}.`;
  return `For “${spec.title}”, my preference is: ${cleanNote}`;
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
  const [selectedId, setSelectedId] = useState("");
  const [customOpen, setCustomOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState<DecisionPromptOption | null | undefined>(undefined);
  const [error, setError] = useState("");

  const selected = useMemo(
    () => spec.options.find((option) => option.id === selectedId) ?? null,
    [selectedId, spec.options],
  );
  const canSubmit = Boolean(onSubmit && !disabled && !sending && (selected || note.trim()));

  async function submit() {
    if (!canSubmit || !onSubmit) return;
    setSending(true);
    setError("");
    try {
      await onSubmit(decisionResponse(spec, selected, note, zh));
      setSubmitted(selected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  }

  const locked = disabled || submitted !== undefined;

  return (
    <section className={`decision-card ${submitted !== undefined ? "is-submitted" : ""}`} aria-label={spec.title}>
      <header className="decision-card-head">
        <span className="decision-card-icon" aria-hidden="true"><MessageSquareText size={16} /></span>
        <div className="decision-card-heading">
          <strong>{spec.title}</strong>
          {spec.description ? <p>{spec.description}</p> : null}
        </div>
      </header>

      <div className="decision-options" role="radiogroup" aria-label={spec.title}>
        {spec.options.map((option) => {
          const active = selectedId === option.id;
          return (
            <button
              key={option.id}
              type="button"
              className={`decision-option ${active ? "is-selected" : ""}`}
              role="radio"
              aria-checked={active}
              disabled={locked}
              onClick={() => {
                setSelectedId(option.id);
                setError("");
              }}
            >
              <span className="decision-option-key">{option.id}</span>
              <span className="decision-option-copy">
                <span className="decision-option-title-row">
                  <strong>{option.title}</strong>
                  {option.recommended ? <em>{zh ? "建议" : "Suggested"}</em> : null}
                </span>
                {option.description ? <span>{option.description}</span> : null}
              </span>
              <span className="decision-option-check" aria-hidden="true">
                {active ? <Check size={14} /> : <ChevronRight size={14} />}
              </span>
            </button>
          );
        })}
      </div>

      {submitted !== undefined ? (
        <div className="decision-submitted" role="status">
          <Check size={14} />
          <span>
            {submitted
              ? (zh ? `已选择 ${submitted.id} · ${submitted.title}` : `Selected ${submitted.id} · ${submitted.title}`)
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
            {error ? <span className="decision-error">{error}</span> : <span className="decision-hint">{zh ? "选择一个方案，也可以补充条件。" : "Choose an option and optionally add guidance."}</span>}
            <button
              type="button"
              className="decision-submit"
              disabled={!canSubmit}
              onClick={() => void submit()}
            >
              <span>{sending ? (zh ? "正在发送…" : "Sending…") : selected ? (zh ? "确认选择" : "Confirm choice") : (zh ? "发送意见" : "Send preference")}</span>
              <Send size={13} />
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

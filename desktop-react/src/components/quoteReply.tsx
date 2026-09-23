import { Reply, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";

export type QuoteReplySource = "assistant" | "user" | "selection";

export interface QuoteReplyDetail {
  text: string;
  source: QuoteReplySource;
  messageId?: string;
}

export const QUOTE_REPLY_EVENT = "loom:quote-reply";
const MAX_QUOTED_CHARS = 6000;

export function dispatchQuoteReply(detail: QuoteReplyDetail): void {
  const text = String(detail.text || "").trim();
  if (!text) return;
  window.dispatchEvent(new CustomEvent<QuoteReplyDetail>(QUOTE_REPLY_EVENT, {
    detail: { ...detail, text },
  }));
}

export function useQuoteReply(): [QuoteReplyDetail | null, (quote: QuoteReplyDetail | null) => void] {
  const [quote, setQuote] = useState<QuoteReplyDetail | null>(null);

  useEffect(() => {
    const onQuote = (event: Event) => {
      const detail = (event as CustomEvent<QuoteReplyDetail>).detail;
      const text = String(detail?.text || "").trim();
      if (!text) return;
      setQuote({ ...detail, text });
    };
    window.addEventListener(QUOTE_REPLY_EVENT, onQuote);
    return () => window.removeEventListener(QUOTE_REPLY_EVENT, onQuote);
  }, []);

  return [quote, setQuote];
}

function normalizedQuoteText(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length <= MAX_QUOTED_CHARS) return trimmed;
  return `${trimmed.slice(0, MAX_QUOTED_CHARS).trimEnd()}…`;
}

export function formatQuotedPrompt(quote: QuoteReplyDetail | null, input: string): string {
  const prompt = input.trim();
  if (!quote?.text.trim()) return prompt;

  const attribution = quote.source === "assistant"
    ? "Loom"
    : quote.source === "user"
      ? "User"
      : "Selection";
  const quoted = normalizedQuoteText(quote.text)
    .split(/\r?\n/)
    .map((line) => line ? `> ${line}` : ">")
    .join("\n");

  return `${quoted}\n> — ${attribution}${prompt ? `\n\n${prompt}` : ""}`;
}

export function QuoteReplyBar({
  quote,
  onClear,
}: {
  quote: QuoteReplyDetail;
  onClear(): void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const source = quote.source === "assistant"
    ? (zh ? "Loom 回复" : "Loom reply")
    : quote.source === "user"
      ? (zh ? "你的消息" : "Your message")
      : (zh ? "所选内容" : "Selection");
  const preview = quote.text.replace(/\s+/g, " ").trim();

  return (
    <div className="composer-quote-reply" role="note" aria-label={zh ? "引用内容" : "Quoted context"}>
      <span className="composer-quote-mark" aria-hidden="true"><Reply size={13.5} strokeWidth={1.8} /></span>
      <span className="composer-quote-copy">
        <span className="composer-quote-label">{zh ? "引用回答" : "Replying to"} · {source}</span>
        <span className="composer-quote-preview" title={preview}>{preview}</span>
      </span>
      <button
        type="button"
        className="composer-quote-clear"
        onClick={onClear}
        title={zh ? "取消引用" : "Remove quote"}
        aria-label={zh ? "取消引用" : "Remove quote"}
      >
        <X size={13.5} strokeWidth={1.9} />
      </button>
    </div>
  );
}

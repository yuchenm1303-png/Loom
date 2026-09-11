import { Check, Copy } from "lucide-react";
import { isValidElement, memo, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { isChatStickerAssetUrl, splitInlineStickerText } from "../chatStickers";
import "katex/dist/katex.min.css";
import "./markdown-message.css";
import "./stickers.css";

interface MarkdownMessageProps {
  content: string;
  compact?: boolean;
}

const STREAM_FRAME_MS = 28;
const STREAM_MAX_STEP = 18;

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) {
    return nodeText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

function languageLabel(children: ReactNode): string {
  const child = Array.isArray(children) ? children[0] : children;
  if (!isValidElement(child)) return "code";
  const className = String((child.props as { className?: string }).className ?? "");
  const match = className.match(/language-([\w+-]+)/i);
  return match?.[1] || "code";
}

function stickerAwareMarkdown(content: string): string {
  const segments = splitInlineStickerText(content);
  return segments
    .map((segment, index) => {
      if (segment.kind === "text") {
        let text = segment.text;
        if (segments[index - 1]?.kind === "sticker" && /^\s*\n(?!\n)/.test(text)) {
          text = text.replace(/^[\t ]*\n[\t ]*/, " ");
        }
        if (segments[index + 1]?.kind === "sticker" && /\n[\t ]*$/.test(text) && !/\n\n[\t ]*$/.test(text)) {
          text = text.replace(/\n[\t ]*$/, " ");
        }
        return text;
      }
      const alt = segment.asset.alt.replace(/\\/g, "\\\\").replace(/\]/g, "\\]");
      return `![${alt}](${segment.asset.url})`;
    })
    .join("");
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = nodeText(children).replace(/\n$/, "");
  const language = languageLabel(children);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="markdown-code-block">
      <div className="markdown-code-head">
        <span>{language}</span>
        <button type="button" onClick={() => void copyCode()} aria-label="Copy code" title="Copy code">
          {copied ? <Check size={12} /> : <Copy size={12} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

const markdownComponents: Components = {
  a({ href, children, ...props }) {
    const external = Boolean(href && /^(https?:|mailto:)/i.test(href));
    return (
      <a
        {...props}
        href={href}
        target={external ? "_blank" : undefined}
        rel={external ? "noopener noreferrer" : undefined}
      >
        {children}
      </a>
    );
  },
  img({ src, alt, className, ...props }) {
    const sticker = isChatStickerAssetUrl(src);
    const classes = [className, sticker ? "assistant-inline-sticker" : ""].filter(Boolean).join(" ");
    return <img {...props} src={src} alt={alt || ""} className={classes || undefined} draggable={sticker ? false : undefined} />;
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
};

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function streamStep(backlog: number): number {
  if (backlog <= 3) return 1;
  if (backlog <= 12) return 2;
  if (backlog <= 32) return 4;
  if (backlog <= 72) return 7;
  if (backlog <= 160) return 11;
  return STREAM_MAX_STEP;
}

/**
 * Keep runtime streaming authoritative while smoothing only what is painted.
 * Providers are free to emit coarse text chunks and React may batch nearby
 * notifications into one paint. Without this presentation buffer, whole words
 * or sentences can visibly pop into place even though item/delta is working.
 *
 * Historical/completed markdown starts at its full value. Only subsequent
 * append-only changes are eased, so reopening a thread never replays a typing
 * animation for old messages.
 */
function useSmoothedMarkdownContent(content: string): string {
  const [visible, setVisible] = useState(content);
  const visibleRef = useRef(content);
  const targetRef = useRef(content);
  const timerRef = useRef<number | null>(null);

  const cancelTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const commit = (value: string) => {
    visibleRef.current = value;
    setVisible(value);
  };

  const schedule = () => {
    if (timerRef.current !== null) return;
    timerRef.current = window.setTimeout(function tick() {
      timerRef.current = null;
      const target = targetRef.current;
      const current = visibleRef.current;
      if (current === target) return;

      if (!target.startsWith(current)) {
        commit(target);
        return;
      }

      const backlog = target.length - current.length;
      const step = Math.min(STREAM_MAX_STEP, streamStep(backlog));
      commit(target.slice(0, current.length + step));
      if (visibleRef.current !== targetRef.current) schedule();
    }, STREAM_FRAME_MS);
  };

  useEffect(() => {
    targetRef.current = content;

    if (prefersReducedMotion()) {
      cancelTimer();
      commit(content);
      return;
    }

    if (!content.startsWith(visibleRef.current)) {
      cancelTimer();
      commit(content);
      return;
    }

    if (content !== visibleRef.current) schedule();
    // Do not cancel the scheduled frame when a denser delta arrives. The live
    // timer reads targetRef, so it naturally chases the newest authoritative
    // text instead of repeatedly restarting before it can paint.
  }, [content]);

  useEffect(() => () => cancelTimer(), []);

  return visible;
}

const MarkdownRenderer = memo(function MarkdownRenderer({ content, compact }: { content: string; compact: boolean }) {
  return (
    <div className={`markdown-body ${compact ? "markdown-compact" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          rehypeKatex,
          [rehypeHighlight, { detect: false, ignoreMissing: true }],
        ]}
        components={markdownComponents}
        skipHtml
      >
        {stickerAwareMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
});

export function MarkdownMessage({ content, compact = false }: MarkdownMessageProps) {
  const visible = useSmoothedMarkdownContent(content);
  return <MarkdownRenderer content={visible} compact={compact} />;
}

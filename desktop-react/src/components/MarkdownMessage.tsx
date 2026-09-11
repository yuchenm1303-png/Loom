import { Check, Copy } from "lucide-react";
import { isValidElement, memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { splitInlineStickerText } from "../chatStickers";
import "katex/dist/katex.min.css";
import "./markdown-message.css";
import "./stickers.css";

interface MarkdownMessageProps {
  content: string;
  compact?: boolean;
}

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
  return splitInlineStickerText(content)
    .map((segment) => {
      if (segment.kind === "text") return segment.text;
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
    const sticker = Boolean(src && src.includes("/chat-stickers/v1/"));
    const classes = [className, sticker ? "assistant-inline-sticker" : ""].filter(Boolean).join(" ");
    return <img {...props} src={src} alt={alt || ""} className={classes || undefined} draggable={sticker ? false : undefined} />;
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
};

export const MarkdownMessage = memo(function MarkdownMessage({ content, compact = false }: MarkdownMessageProps) {
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

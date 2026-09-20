import { Check, Copy, ExternalLink, Maximize2, X } from "lucide-react";
import { isValidElement, memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { isChatStickerAssetUrl, splitInlineStickerText } from "../chatStickers";
import "katex/dist/katex.min.css";
import "./markdown-message.css";
import "./user-message-attachments.css";
import "./stickers.css";
import { useStreamingPresentation } from "./StreamingPresentation";
import { streamingGraphemes } from "./streamingText";

interface MarkdownMessageProps {
  content: string;
  compact?: boolean;
  streaming?: boolean;
  messageKey?: string;
  interrupted?: boolean;
  workspace?: string;
}

interface LocalImagePayload {
  dataUrl: string;
  path: string;
  name: string;
  size: number;
  mimeType: string;
}

const LOCAL_IMAGE_SUFFIX = /\.(?:png|jpe?g|gif|webp|bmp)$/i;

function localImagePath(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw || /^(?:https?:|data:|blob:)/i.test(raw)) return "";
  const clean = raw.replace(/^<|>$/g, "").split(/[?#]/, 1)[0];
  if (!LOCAL_IMAGE_SUFFIX.test(clean)) return "";
  try {
    return decodeURI(clean);
  } catch {
    return clean;
  }
}

function singleLineImagePath(value: string): string {
  const trimmed = String(value || "").trim();
  if (!trimmed || trimmed.includes("\n")) return "";
  return localImagePath(trimmed);
}

function markdownUrlTransform(url: string, _key: string, node: { tagName?: string }): string {
  if (node.tagName === "img" && localImagePath(url)) return url;
  return defaultUrlTransform(url);
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

function LocalImagePreview({
  source,
  alt,
  workspace,
  compact = false,
}: {
  source: string;
  alt?: string;
  workspace?: string;
  compact?: boolean;
}) {
  const [payload, setPayload] = useState<LocalImagePayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const target = localImagePath(source);

  useEffect(() => {
    let disposed = false;
    setPayload(null);
    setFailed(false);
    setPreviewing(false);
    if (!target || !workspace) {
      setFailed(true);
      return () => { disposed = true; };
    }
    void window.loom.readLocalImage(target, workspace)
      .then((next) => {
        if (!disposed) setPayload(next);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });
    return () => { disposed = true; };
  }, [target, workspace]);

  useEffect(() => {
    if (!previewing) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewing(false);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [previewing]);

  if (!target || !workspace || failed) {
    return (
      <span className="assistant-local-image-fallback" title={target || source}>
        图片预览不可用
      </span>
    );
  }

  if (!payload) {
    return <span className="assistant-local-image-loading" role="status">正在加载图片…</span>;
  }

  const label = alt?.trim() || payload.name;
  const lightbox = previewing ? createPortal(
    <div
      className="user-message-image-lightbox"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setPreviewing(false);
      }}
    >
      <div className="user-message-image-lightbox-panel" role="dialog" aria-modal="true" aria-label={`查看图片 ${label}`}>
        <div className="user-message-image-lightbox-toolbar">
          <strong title={payload.path}>{label}</strong>
          <div className="user-message-image-lightbox-actions">
            <button type="button" onClick={() => void window.loom.revealPath(payload.path)} title="在文件夹中查看">
              <ExternalLink size={15} strokeWidth={1.8} />
              <span>原文件</span>
            </button>
            <button type="button" className="icon-only" onClick={() => setPreviewing(false)} title="关闭图片预览" aria-label="关闭图片预览">
              <X size={17} strokeWidth={1.9} />
            </button>
          </div>
        </div>
        <div className="user-message-image-lightbox-canvas">
          <img src={payload.dataUrl} alt={label} draggable={false} />
        </div>
      </div>
    </div>,
    document.body,
  ) : null;

  return (
    <>
      <button
        type="button"
        className={`assistant-local-image-preview ${compact ? "is-compact" : ""}`}
        onClick={() => setPreviewing(true)}
        title={`${label} · 点击放大`}
        aria-label={`放大查看图片：${label}`}
      >
        <img src={payload.dataUrl} alt={label} loading="lazy" decoding="async" draggable={false} />
        <span className="assistant-local-image-hint" aria-hidden="true">
          <Maximize2 size={14} strokeWidth={1.8} />
          <span>查看</span>
        </span>
      </button>
      {lightbox}
    </>
  );
}

function CodeBlock({ children, workspace }: { children?: ReactNode; workspace?: string }) {
  const [copied, setCopied] = useState(false);
  const code = nodeText(children).replace(/\n$/, "");
  const language = languageLabel(children);
  const imagePath = singleLineImagePath(code);

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
    <>
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
      {imagePath && workspace ? (
        <LocalImagePreview source={imagePath} workspace={workspace} />
      ) : null}
    </>
  );
}

function markdownComponents(workspace?: string): Components {
  return {
    p({ children }) {
      const imagePath = singleLineImagePath(nodeText(children));
      return (
        <>
          <p>{children}</p>
          {imagePath && workspace ? (
            <LocalImagePreview source={imagePath} workspace={workspace} />
          ) : null}
        </>
      );
    },
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
      if (sticker) {
        const classes = [className, "assistant-inline-sticker"].filter(Boolean).join(" ");
        return <img {...props} src={src} alt={alt || ""} className={classes} draggable={false} />;
      }
      const local = localImagePath(src);
      if (local) {
        return <LocalImagePreview source={local} alt={alt || ""} workspace={workspace} />;
      }
      const classes = [className, "assistant-markdown-image"].filter(Boolean).join(" ");
      return <img {...props} src={src} alt={alt || ""} className={classes} loading="lazy" decoding="async" />;
    },
    pre({ children }) {
      return <CodeBlock workspace={workspace}>{children}</CodeBlock>;
    },
  };
}

interface StreamNode {
  type: string;
  tagName?: string;
  value?: string;
  position?: { start: { offset?: number } };
  properties?: Record<string, unknown>;
  children?: StreamNode[];
}

function rehypeStreamText(options: { enabled: boolean; fadeFrom: number }) {
  return (tree: StreamNode) => {
    if (!options.enabled) return;
    function visit(node: StreamNode) {
      if (!node.children || ["pre", "code", "math", "svg"].includes(node.tagName || "")) return;
      node.children = node.children.flatMap((child): StreamNode[] => {
        if (child.type !== "text" || !child.value?.trim()) {
          visit(child);
          return [child];
        }
        const characters = streamingGraphemes(child.value);
        let offset = child.position?.start.offset ?? 0;
        const chunks: StreamNode[] = [];
        for (let i = 0; i < characters.length; i++) {
          chunks.push({
            type: "element", tagName: "span",
            properties: { className: [offset >= options.fadeFrom ? "stream-text-chunk" : "stream-text-settled"] },
            children: [{ type: "text", value: characters[i] }],
          });
          offset += characters[i].length;
        }
        return chunks;
      });
    }
    visit(tree);
  };
}

const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  compact,
  workspace,
  streaming,
  receiving,
  fadeFrom,
}: {
  content: string;
  compact: boolean;
  workspace?: string;
  streaming: boolean;
  receiving: boolean;
  fadeFrom: number;
}) {
  const components = useMemo(() => markdownComponents(workspace), [workspace]);
  return (
    <div className={`markdown-body ${compact ? "markdown-compact" : ""} ${streaming ? "is-streaming" : ""} ${receiving ? "is-receiving" : ""}`} aria-busy={receiving}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          [rehypeStreamText, { enabled: streaming, fadeFrom }],
          rehypeKatex,
          [rehypeHighlight, { detect: false, ignoreMissing: true }],
        ]}
        components={components}
        urlTransform={markdownUrlTransform}
        skipHtml
      >
        {stickerAwareMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
});

export function MarkdownMessage({ content, compact = false, workspace, streaming = false, messageKey, interrupted = false }: MarkdownMessageProps) {
  const presentation = useStreamingPresentation(content, streaming, messageKey, interrupted);
  return <MarkdownRenderer content={presentation.visible} compact={compact} workspace={workspace}
    streaming={presentation.painting} receiving={streaming && !interrupted} fadeFrom={presentation.fadeFrom} />;
}

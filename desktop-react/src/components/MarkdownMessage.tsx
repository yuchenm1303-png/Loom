import { Check, Copy, Maximize2 } from "lucide-react";
import { isValidElement, memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { ImageLightbox } from "./ImageLightbox";
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
import { TerminalRunControl } from "./TerminalRunControl";

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

function localWorkspacePath(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw || raw.startsWith("#") || raw.startsWith("//")) return "";
  const windowsAbsolute = /^[A-Za-z]:[\\/]/.test(raw);
  if (!windowsAbsolute && /^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return "";
  const clean = raw.replace(/^<|>$/g, "").split(/[?#]/, 1)[0];
  try {
    return decodeURI(clean);
  } catch {
    return clean;
  }
}

function markdownUrlTransform(url: string, _key: string, node: { tagName?: string }): string {
  if (node.tagName === "img" && localImagePath(url)) return url;
  if (node.tagName === "a" && localWorkspacePath(url)) return url;
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
  const lightbox = <ImageLightbox open={previewing} source={payload.dataUrl} label={label} path={payload.path}
    onClose={() => setPreviewing(false)} onReveal={() => { void window.loom.revealPath(payload.path); }} />;

  return (
    <>
      <button
        type="button"
        className={`assistant-local-image-preview ${compact ? "is-compact" : ""}`}
        onClick={() => setPreviewing(true)}
        title={`${label} · 点击放大`}
        aria-label={`放大查看图片：${label}`}
      >
        <img src={payload.dataUrl} alt={label} loading="lazy" decoding="async" draggable={false} data-loom-image-path={payload.path} />
        <span className="assistant-local-image-hint" aria-hidden="true">
          <Maximize2 size={14} strokeWidth={1.8} />
          <span>查看</span>
        </span>
      </button>
      {lightbox}
    </>
  );
}

function CodeBlock({
  children,
  workspace,
  executionDisabled = false,
}: {
  children?: ReactNode;
  workspace?: string;
  executionDisabled?: boolean;
}) {
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
        <TerminalRunControl
          language={language}
          command={code}
          workspace={workspace}
          disabled={executionDisabled}
        />
      </div>
      {imagePath && workspace ? (
        <LocalImagePreview source={imagePath} workspace={workspace} />
      ) : null}
    </>
  );
}

function markdownComponents(workspace?: string, executionDisabled = false): Components {
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
      const localTarget = localWorkspacePath(href);
      const fragment = Boolean(href?.startsWith("#"));
      return (
        <a
          {...props}
          href={external || fragment ? href : localTarget ? "#" : href}
          target={undefined}
          rel={external ? "noopener noreferrer" : undefined}
          data-loom-local-artifact={localTarget || undefined}
          onClick={(event) => {
            if (external && href) {
              event.preventDefault();
              void window.loom.openExternal(href);
              return;
            }
            if (localTarget) {
              event.preventDefault();
              if (workspace) {
                window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
                  detail: { path: localTarget, workspace },
                }));
              }
              return;
            }
            if (href && !fragment) event.preventDefault();
          }}
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
      return <CodeBlock workspace={workspace} executionDisabled={executionDisabled}>{children}</CodeBlock>;
    },
  };
}

interface StreamNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: StreamNode[];
}

interface StreamTailOptions {
  enabled: boolean;
}

interface StreamTailMatch {
  parent: StreamNode;
  childIndex: number;
}

const STREAM_TAIL_GRAPHEMES = 6;
const STREAM_TAIL_BLOCKED = new Set(["pre", "code", "math", "svg"]);

function findStreamingTail(node: StreamNode): StreamTailMatch | null {
  if (node.tagName && STREAM_TAIL_BLOCKED.has(node.tagName)) return null;
  const children = node.children;
  if (!children) return null;

  // Only the final eligible node is needed; walking from the end avoids
  // revisiting every completed row of a growing table on each paint.
  for (let index = children.length - 1; index >= 0; index -= 1) {
    const child = children[index];
    if (child.type === "text" && child.value?.trim()) {
      return { parent: node, childIndex: index };
    }
    const nested = findStreamingTail(child);
    if (nested) return nested;
  }
  return null;
}

/**
 * Give only the newest visible prose a soft reveal without recreating the old
 * per-character DOM. The transform finds the last eligible text node and wraps
 * at most six graphemes in one stable span. Its entrance plays once per stream,
 * rather than dimming already-read characters again on every presentation tick.
 */
function rehypeStreamingTail(options: StreamTailOptions) {
  return (tree: StreamNode) => {
    if (!options.enabled) return;

    const match = findStreamingTail(tree);
    if (!match) return;
    const children = match.parent.children;
    if (!children) return;

    const target = children[match.childIndex];
    const value = String(target.value ?? "");
    const graphemes = streamingGraphemes(value);
    if (!graphemes.length) return;

    const splitAt = Math.max(0, graphemes.length - STREAM_TAIL_GRAPHEMES);
    const prefix = graphemes.slice(0, splitAt).join("");
    const tail = graphemes.slice(splitAt).join("");
    const replacement: StreamNode[] = [];
    if (prefix) replacement.push({ type: "text", value: prefix });
    replacement.push({
      type: "element",
      tagName: "span",
      properties: { className: ["stream-text-tail"] },
      children: [{ type: "text", value: tail }],
    });
    children.splice(match.childIndex, 1, ...replacement);
  };
}

const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  compact,
  workspace,
  streaming,
  receiving,
}: {
  content: string;
  compact: boolean;
  workspace?: string;
  streaming: boolean;
  receiving: boolean;
}) {
  const components = useMemo(() => markdownComponents(workspace, receiving), [workspace, receiving]);
  return (
    <div className={`markdown-body ${compact ? "markdown-compact" : ""} ${streaming ? "is-streaming" : ""} ${receiving ? "is-receiving" : ""}`} aria-busy={receiving}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          [rehypeStreamingTail, { enabled: streaming || receiving }],
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
    streaming={presentation.painting} receiving={streaming && !interrupted} />;
}

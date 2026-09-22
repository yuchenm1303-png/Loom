import { Check, Copy, ExternalLink, Globe2 } from "lucide-react";
import { isValidElement, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import "./user-rich-text.css";

function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function languageLabel(children: ReactNode): string {
  const values = Array.isArray(children) ? children : [children];
  for (const child of values) {
    if (!isValidElement<{ className?: string }>(child)) continue;
    const match = String(child.props.className || "").match(/language-([\w+-]+)/i);
    if (match) return match[1];
  }
  return "code";
}

function UserCodeBlock({ children }: { children?: ReactNode }) {
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
    <div className="user-rich-code-block">
      <div className="user-rich-code-head">
        <span>{language}</span>
        <button type="button" onClick={() => void copyCode()} title="Copy code" aria-label="Copy code">
          {copied ? <Check size={12} /> : <Copy size={12} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function safeUrlTransform(url: string): string {
  if (/^(https?:|mailto:)/i.test(url)) return url;
  return defaultUrlTransform(url);
}

function components(): Components {
  return {
    a({ href, children, ...props }) {
      const external = Boolean(href && /^(https?:|mailto:)/i.test(href));
      return <a {...props} href={href} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer" : undefined}>{children}</a>;
    },
    img({ src, alt }) {
      // Do not auto-fetch remote Markdown images from user text. Besides noisy
      // layout, a tracking pixel in pasted Markdown should not receive a request
      // merely because a conversation was reopened.
      const label = alt || src || "image";
      return src && /^https?:/i.test(src)
        ? <a href={src} target="_blank" rel="noopener noreferrer">[{label}]</a>
        : <span>[{label}]</span>;
    },
    pre({ children }) {
      return <UserCodeBlock>{children}</UserCodeBlock>;
    },
  };
}

function standaloneLinks(source: string): { href: string; host: string; detail: string }[] {
  const seen = new Set<string>();
  const result: { href: string; host: string; detail: string }[] = [];
  for (const line of source.split(/\r?\n/)) {
    const value = line.trim();
    if (!/^https?:\/\/\S+$/i.test(value) || seen.has(value)) continue;
    try {
      const url = new URL(value);
      seen.add(value);
      result.push({ href: value, host: url.hostname.replace(/^www\./, ""), detail: `${url.pathname}${url.search}` || "/" });
    } catch {
      // Invalid links stay ordinary Markdown text.
    }
  }
  return result.slice(0, 6);
}

function withoutStandaloneLinks(source: string, links: { href: string }[]): string {
  if (!links.length) return source;
  const values = new Set(links.map((link) => link.href));
  return source.split(/\r?\n/).filter((line) => !values.has(line.trim())).join("\n").trim();
}

export function UserRichText({ text }: { text: string }) {
  const links = useMemo(() => standaloneLinks(text), [text]);
  const markdown = useMemo(() => withoutStandaloneLinks(text, links), [links, text]);
  const renderer = useMemo(() => components(), []);

  return (
    <div className="user-rich-message">
      {markdown ? (
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: false, ignoreMissing: true }]]}
          components={renderer}
          urlTransform={safeUrlTransform}
          skipHtml
        >
          {markdown}
        </ReactMarkdown>
      ) : null}
      {links.length ? (
        <div className="user-link-cards">
          {links.map((link) => (
            <a key={link.href} className="user-link-card" href={link.href} target="_blank" rel="noopener noreferrer">
              <span className="user-link-icon"><Globe2 size={15} /></span>
              <span className="user-link-copy">
                <strong>{link.host}</strong>
                <span title={link.href}>{link.detail}</span>
              </span>
              <ExternalLink size={13} className="user-link-open" />
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}

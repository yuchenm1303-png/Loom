import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Maximize2,
  Music2,
  Play,
  Video,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import "./user-message-attachments.css";
import { UserRichText } from "./UserRichText";

export interface DisplayAttachment {
  name: string;
  path: string;
  kind: "image" | "file";
  extension: string;
  extractedPath?: string;
}

export interface ParsedUserMessage {
  text: string;
  attachments: DisplayAttachment[];
}

const MANIFEST_HEADER = "Attached files (already saved in this workspace):";
const IMAGE_MARKER = /^\[\d+ images? attached\]$/i;
const MANIFEST_LINE = /^-\s+(.+?)\s+—\s+(\.loom\/attachments\/.+?)\s+\((image, shown above|read it with the file tools)(?:;\s+extracted text:\s+(\.loom\/attachments\/.+?\.extracted\.txt))?\)$/i;
const LONG_MESSAGE_CHAR_THRESHOLD = 420;
const LONG_MESSAGE_LINE_THRESHOLD = 9;
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "m4v"]);
const INLINE_VIEW_EXTENSIONS = new Set([
  "pdf", "txt", "md", "log", "json", "xml", "csv", "html", "htm", "css", "scss", "js", "jsx", "ts", "tsx",
  "py", "java", "kt", "kts", "go", "rs", "c", "h", "cpp", "hpp", "cs", "php", "rb", "swift", "vue", "svelte",
  "yaml", "yml", "toml", "sh", "ps1", "sql",
]);

function extensionOf(name: string): string {
  const clean = String(name || "").trim();
  const dot = clean.lastIndexOf(".");
  return dot >= 0 ? clean.slice(dot + 1).toLowerCase() : "";
}

export function parseUserMessageContent(raw: string): ParsedUserMessage {
  const source = String(raw ?? "").replace(/\r\n/g, "\n");
  const markerIndex = source.indexOf(MANIFEST_HEADER);
  if (markerIndex < 0) return { text: source, attachments: [] };

  const before = source.slice(0, markerIndex).trimEnd();
  const manifest = source.slice(markerIndex + MANIFEST_HEADER.length).trim();
  const attachments: DisplayAttachment[] = [];
  const unparsed: string[] = [];

  for (const line of manifest.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || IMAGE_MARKER.test(trimmed)) continue;
    const match = MANIFEST_LINE.exec(trimmed);
    if (!match) {
      unparsed.push(line);
      continue;
    }
    const name = match[1].trim();
    attachments.push({
      name,
      path: match[2].trim(),
      kind: match[3].toLowerCase().startsWith("image") ? "image" : "file",
      extension: extensionOf(name),
      extractedPath: match[4]?.trim() || undefined,
    });
  }

  // If the manifest shape changes upstream, keep the original text rather than
  // accidentally hiding user-visible content.
  if (!attachments.length || unparsed.some((line) => line.trim())) {
    return { text: source, attachments: [] };
  }

  return { text: before.trim(), attachments };
}

function iconFor(attachment: DisplayAttachment): LucideIcon {
  if (attachment.kind === "image") return FileImage;
  if (["zip", "rar", "7z", "tar", "gz", "bz2", "xz"].includes(attachment.extension)) return FileArchive;
  if (AUDIO_EXTENSIONS.has(attachment.extension)) return Music2;
  if (VIDEO_EXTENSIONS.has(attachment.extension)) return Video;
  if (["csv", "xls", "xlsx", "ods"].includes(attachment.extension)) return FileSpreadsheet;
  if ([
    "js", "jsx", "ts", "tsx", "py", "java", "kt", "kts", "go", "rs", "c", "h", "cpp", "hpp",
    "cs", "php", "rb", "swift", "vue", "svelte", "html", "css", "scss", "json", "yaml", "yml", "toml",
    "xml", "sh", "ps1", "sql",
  ].includes(attachment.extension)) return FileCode2;
  if (["txt", "md", "pdf", "doc", "docx", "rtf", "log"].includes(attachment.extension)) return FileText;
  return File;
}

function typeLabel(attachment: DisplayAttachment): string {
  return attachment.extension ? attachment.extension.toUpperCase() : "文件";
}

function isLongUserMessage(text: string): boolean {
  const normalized = String(text || "").trim();
  if (!normalized) return false;
  if (normalized.length > LONG_MESSAGE_CHAR_THRESHOLD) return true;
  return normalized.split("\n").length >= LONG_MESSAGE_LINE_THRESHOLD;
}

function workspacePathFromHeader(): string {
  return String(document.querySelector<HTMLElement>(".workspace-full-path")?.textContent || "").trim();
}

function attachmentAbsolutePath(attachment: DisplayAttachment): string {
  const workspace = workspacePathFromHeader();
  if (!workspace) return "";
  const root = workspace.replaceAll("\\", "/").replace(/\/+$/, "");
  const relative = attachment.path.replaceAll("\\", "/").replace(/^\.\//, "").replace(/^\/+/, "");
  if (!relative.startsWith(".loom/attachments/")) return "";
  return `${root}/${relative}`;
}

function attachmentFileUrl(attachment: DisplayAttachment): string {
  const absolute = attachmentAbsolutePath(attachment);
  if (!absolute) return "";
  const encoded = encodeURI(absolute).replaceAll("#", "%23").replaceAll("?", "%3F");
  return /^[A-Za-z]:\//.test(absolute) ? `file:///${encoded}` : `file://${encoded}`;
}

async function revealAttachment(attachment: DisplayAttachment): Promise<void> {
  const absolute = attachmentAbsolutePath(attachment);
  if (!absolute) return;
  try {
    await window.loom.revealPath(absolute);
  } catch {
    // The attachment remains visible in the transcript even if its staged copy
    // was removed from disk later.
  }
}

function viewAttachment(attachment: DisplayAttachment): void {
  const source = attachmentFileUrl(attachment);
  if (source && (attachment.kind === "image" || INLINE_VIEW_EXTENSIONS.has(attachment.extension))) {
    window.open(source, "_blank", "noopener,noreferrer");
    return;
  }
  void revealAttachment(attachment);
}

function FileAttachmentCard({ attachment }: { attachment: DisplayAttachment }) {
  const Icon = iconFor(attachment);
  const inlineView = INLINE_VIEW_EXTENSIONS.has(attachment.extension);
  const actionLabel = inlineView ? "点击查看附件" : "点击在系统中查看附件";

  return (
    <button
      type="button"
      className="user-message-attachment-card is-clickable"
      data-loom-file-path={attachmentAbsolutePath(attachment)}
      title={`${attachment.name} · ${actionLabel}`}
      onClick={() => viewAttachment(attachment)}
      aria-label={`${actionLabel}：${attachment.name}`}
    >
      <span className="user-message-file-icon" aria-hidden="true"><Icon size={18} strokeWidth={1.65} /></span>
      <span className="user-message-file-copy">
        <strong>{attachment.name}</strong>
        <span>{attachment.kind === "image" ? "图片" : `${typeLabel(attachment)}${attachment.extractedPath ? " · 已解析" : ""}`}</span>
      </span>
      <span className="user-message-file-open" aria-hidden="true"><ExternalLink size={14} strokeWidth={1.8} /></span>
    </button>
  );
}

function MediaAttachmentPreview({ attachment, workspace }: { attachment: DisplayAttachment; workspace?: string }) {
  const video = VIDEO_EXTENSIONS.has(attachment.extension);
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  async function load(): Promise<void> {
    if (loading || source) return;
    const workspaceRoot = String(workspace || workspacePathFromHeader()).trim();
    if (!workspaceRoot) {
      setFailed(true);
      return;
    }
    setLoading(true);
    setFailed(false);
    try {
      const result = await window.loom.readLocalMedia(attachment.path, workspaceRoot);
      setSource(result.dataUrl);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  if (source) {
    return (
      <div className={`user-message-media-preview ${video ? "is-video" : "is-audio"}`} data-loom-file-path={attachmentAbsolutePath(attachment)}>
        <div className="user-message-media-head">
          {video ? <Video size={14} /> : <Music2 size={14} />}
          <strong title={attachment.name}>{attachment.name}</strong>
          <button type="button" onClick={() => void revealAttachment(attachment)} title="在文件夹中查看">
            <ExternalLink size={13} />
          </button>
        </div>
        {video
          ? <video src={source} controls preload="metadata" playsInline />
          : <audio src={source} controls preload="metadata" />}
      </div>
    );
  }

  return (
    <button
      type="button"
      className="user-message-attachment-card is-clickable is-media"
      data-loom-file-path={attachmentAbsolutePath(attachment)}
      onClick={() => void load()}
      title={failed ? "无法内联预览，点击重试" : `播放 ${attachment.name}`}
    >
      <span className="user-message-file-icon" aria-hidden="true">
        {video ? <Video size={18} /> : <Music2 size={18} />}
      </span>
      <span className="user-message-file-copy">
        <strong>{attachment.name}</strong>
        <span>{failed ? "预览不可用" : loading ? "正在载入…" : video ? "视频" : "音频"}</span>
      </span>
      <span className="user-message-file-open" aria-hidden="true"><Play size={13} /></span>
    </button>
  );
}

function ImageAttachmentPreview({ attachment, workspace }: { attachment: DisplayAttachment; workspace?: string }) {
  const [source, setSource] = useState("");
  const [failed, setFailed] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSource("");
    setFailed(false);
    setPreviewing(false);

    const workspaceRoot = String(workspace || workspacePathFromHeader()).trim();
    if (!workspaceRoot) {
      setFailed(true);
      return () => {
        cancelled = true;
      };
    }

    void window.loom.readLocalImage(attachment.path, workspaceRoot)
      .then((result) => {
        if (!cancelled) setSource(result.dataUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [attachment.path, workspace]);

  useEffect(() => {
    if (!previewing) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewing(false);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [previewing]);

  if (!source || failed) return <FileAttachmentCard attachment={attachment} />;

  const lightbox = previewing ? createPortal(
    <div
      className="user-message-image-lightbox"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setPreviewing(false);
      }}
    >
      <div className="user-message-image-lightbox-panel" role="dialog" aria-modal="true" aria-label={`查看图片 ${attachment.name}`}>
        <div className="user-message-image-lightbox-toolbar">
          <strong title={attachment.name}>{attachment.name}</strong>
          <div className="user-message-image-lightbox-actions">
            <button type="button" onClick={() => void revealAttachment(attachment)} title="在文件夹中查看">
              <ExternalLink size={15} strokeWidth={1.8} />
              <span>原文件</span>
            </button>
            <button type="button" className="icon-only" onClick={() => setPreviewing(false)} title="关闭图片预览" aria-label="关闭图片预览">
              <X size={17} strokeWidth={1.9} />
            </button>
          </div>
        </div>
        <div className="user-message-image-lightbox-canvas">
          <img src={source} alt={attachment.name} draggable={false} data-loom-image-path={attachmentAbsolutePath(attachment)} />
        </div>
      </div>
    </div>,
    document.body,
  ) : null;

  return (
    <>
      <button
        type="button"
        className="user-message-image-preview"
        title={`${attachment.name} · 点击放大`}
        onClick={() => setPreviewing(true)}
        aria-label={`放大查看图片：${attachment.name}`}
      >
        <img
          src={source}
          alt={attachment.name}
          loading="lazy"
          decoding="async"
          data-loom-image-path={attachmentAbsolutePath(attachment)}
          onError={() => setFailed(true)}
        />
        <span className="user-message-image-open-hint" aria-hidden="true">
          <Maximize2 size={14} strokeWidth={1.8} />
          <span>查看</span>
        </span>
      </button>
      {lightbox}
    </>
  );
}

export function UserMessageContent({ parsed, workspace }: { parsed: ParsedUserMessage; workspace?: string }) {
  const collapsible = useMemo(() => isLongUserMessage(parsed.text), [parsed.text]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setExpanded(false);
  }, [parsed.text]);

  return (
    <div className="user-message-content">
      {parsed.attachments.length ? (
        <div className="user-message-attachment-list" aria-label="Attached files">
          {parsed.attachments.map((attachment, index) => {
            const key = `${attachment.path}-${index}`;
            if (attachment.kind === "image") {
              return <ImageAttachmentPreview attachment={attachment} workspace={workspace} key={key} />;
            }
            if (AUDIO_EXTENSIONS.has(attachment.extension) || VIDEO_EXTENSIONS.has(attachment.extension)) {
              return <MediaAttachmentPreview attachment={attachment} workspace={workspace} key={key} />;
            }
            return <FileAttachmentCard attachment={attachment} key={key} />;
          })}
        </div>
      ) : null}
      {parsed.text ? (
        <div className={`user-message-copy-shell ${collapsible ? "is-collapsible" : ""} ${expanded ? "is-expanded" : "is-collapsed"}`}>
          <div className="user-message-text"><UserRichText text={parsed.text} /></div>
          {collapsible ? (
            <button
              type="button"
              className="user-message-collapse-toggle"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              title={expanded ? "收起长消息" : "展开完整消息"}
            >
              <span>{expanded ? "收起" : "展开全部"}</span>
              {expanded
                ? <ChevronUp size={14} strokeWidth={1.9} aria-hidden="true" />
                : <ChevronDown size={14} strokeWidth={1.9} aria-hidden="true" />}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

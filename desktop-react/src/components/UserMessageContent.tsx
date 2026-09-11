import {
  ChevronDown,
  ChevronUp,
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import "./user-message-attachments.css";

export interface DisplayAttachment {
  name: string;
  path: string;
  kind: "image" | "file";
  extension: string;
}

export interface ParsedUserMessage {
  text: string;
  attachments: DisplayAttachment[];
}

const MANIFEST_HEADER = "Attached files (already saved in this workspace):";
const IMAGE_MARKER = /^\[\d+ images? attached\]$/i;
const MANIFEST_LINE = /^-\s+(.+?)\s+—\s+(\.loom\/attachments\/.+?)\s+\((image, shown above|read it with the file tools)\)$/i;
const LONG_MESSAGE_CHAR_THRESHOLD = 420;
const LONG_MESSAGE_LINE_THRESHOLD = 9;

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
  if (attachment.kind === "image") return "图片";
  return attachment.extension ? attachment.extension.toUpperCase() : "文件";
}

function isLongUserMessage(text: string): boolean {
  const normalized = String(text || "").trim();
  if (!normalized) return false;
  if (normalized.length > LONG_MESSAGE_CHAR_THRESHOLD) return true;
  return normalized.split("\n").length >= LONG_MESSAGE_LINE_THRESHOLD;
}

export function UserMessageContent({ parsed }: { parsed: ParsedUserMessage }) {
  const collapsible = useMemo(() => isLongUserMessage(parsed.text), [parsed.text]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setExpanded(false);
  }, [parsed.text]);

  return (
    <div className="user-message-content">
      {parsed.text ? (
        <div className={`user-message-copy-shell ${collapsible ? "is-collapsible" : ""} ${expanded ? "is-expanded" : "is-collapsed"}`}>
          <div className="user-message-text">{parsed.text}</div>
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
      {parsed.attachments.length ? (
        <div className="user-message-attachment-list" aria-label="Attached files">
          {parsed.attachments.map((attachment, index) => {
            const Icon = iconFor(attachment);
            return (
              <div className="user-message-attachment-card" key={`${attachment.path}-${index}`} title={attachment.name}>
                <span className="user-message-file-icon" aria-hidden="true"><Icon size={18} strokeWidth={1.65} /></span>
                <span className="user-message-file-copy">
                  <strong>{attachment.name}</strong>
                  <span>{typeLabel(attachment)}</span>
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

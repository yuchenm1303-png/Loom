import {
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Music2,
  Video,
  X,
  type LucideIcon,
} from "lucide-react";
import type { Attachment } from "../types/loom";
import { extensionOf, formatAttachmentSize } from "./composerAttachments";

const CODE = new Set([".js",".jsx",".ts",".tsx",".py",".java",".kt",".go",".rs",".c",".h",".cpp",".hpp",".cs",".php",".rb",".swift",".vue",".svelte",".html",".css",".scss",".json",".yaml",".yml",".toml",".xml",".sh",".ps1",".sql"]);
const ARCHIVE = new Set([".zip",".rar",".7z",".tar",".gz",".bz2",".xz"]);
const SHEET = new Set([".csv",".xls",".xlsx",".ods"]);
const AUDIO = new Set([".mp3",".wav",".m4a",".aac",".flac",".ogg",".opus"]);
const VIDEO_EXT = new Set([".mp4",".webm",".mov",".m4v",".avi",".mkv"]);

function iconFor(item: Attachment): LucideIcon {
  const extension = extensionOf(item.name);
  if (item.isImage) return FileImage;
  if (CODE.has(extension)) return FileCode2;
  if (ARCHIVE.has(extension)) return FileArchive;
  if (SHEET.has(extension)) return FileSpreadsheet;
  if (AUDIO.has(extension)) return Music2;
  if (VIDEO_EXT.has(extension)) return Video;
  if ([".txt",".md",".pdf",".doc",".docx",".rtf",".log",".ppt",".pptx"].includes(extension)) return FileText;
  return File;
}

export function ComposerAttachmentStrip({
  attachments,
  imagesAllowed,
  onRemove,
}: {
  attachments: Attachment[];
  imagesAllowed: boolean;
  onRemove(id: string): void;
}) {
  if (!attachments.length) return null;
  return (
    <div className="composer-attachments" aria-label="Attachments">
      {attachments.map((item) => {
        const blocked = item.isImage && !imagesAllowed;
        const Icon = iconFor(item);
        return (
          <span
            key={item.id}
            className={`composer-attachment ${item.previewUrl ? "is-image-preview" : ""} ${blocked ? "is-blocked" : ""}`}
            title={blocked ? "This model cannot read images" : item.path}
          >
            {item.previewUrl ? (
              <img src={item.previewUrl} alt="" className="composer-attachment-thumb" />
            ) : (
              <Icon size={13} />
            )}
            {!item.previewUrl ? (
              <>
                <span className="composer-attachment-name">{item.name}</span>
                {formatAttachmentSize(item.size) ? (
                  <span className="composer-attachment-size">{formatAttachmentSize(item.size)}</span>
                ) : null}
              </>
            ) : null}
            <button type="button" onClick={() => onRemove(item.id)} aria-label={`Remove ${item.name}`}>
              <X size={12} />
            </button>
          </span>
        );
      })}
    </div>
  );
}

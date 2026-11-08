import { useRef, useState } from "react";
import { AnimatedFold } from "../motion/AnimatedFold";
import { MotionRows, MotionValue } from "./MotionRows";
import { File, FileImage, FileArchive, FileCode2, FileSpreadsheet, FileText, Music2, Video, RotateCw, X, Check, CircleAlert } from "../components/icons";
import { ImageLightbox } from "../components/ImageLightbox";
import { fileSize, type AttachmentQueue, type MobileAttachment } from "./attachmentQueue";

function fileIcon(file: File) {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (file.type.startsWith("image/")) return FileImage;
  if (file.type.startsWith("audio/")) return Music2;
  if (file.type.startsWith("video/")) return Video;
  if (/^(zip|rar|7z|gz|tar)$/.test(ext)) return FileArchive;
  if (/^(xlsx?|csv|ods)$/.test(ext)) return FileSpreadsheet;
  if (/^(js|jsx|ts|tsx|py|rs|go|json|html|css|java|kt)$/.test(ext)) return FileCode2;
  if (/^(pdf|docx?|txt|md|rtf)$/.test(ext)) return FileText;
  return File;
}

export function AttachmentTray({ items, queue, online, disabled }: {
  items: MobileAttachment[]; queue: AttachmentQueue; online: boolean; disabled: boolean;
}) {
  const [preview, setPreview] = useState<number | null>(null);
  const [failedPreviews, setFailedPreviews] = useState<Set<number>>(() => new Set());
  const selected = items.find((item) => item.id === preview);
  const image = useRef({ source: "", label: "" });
  if (selected?.preview) image.current = { source: selected.preview, label: selected.file.name };
  const ready = items.filter((item) => item.status === "ready").length;
  return <><AnimatedFold open={Boolean(items.length)} fade={false}><div className="m-attachment-tray" aria-label="待发送附件">
    <p className="m-attachment-summary" role="status">{items.length} / 10 个附件 · {fileSize(items.reduce((total, item) => total + item.file.size, 0))} / 50 MB · {ready === items.length ? "已就绪" : `${ready} 个已就绪`}</p>
    <ul className="m-attachment-list">
      <MotionRows items={items} itemKey={item => String(item.id)} as="li">{(item) => {
        const Icon = fileIcon(item.file);
        const busy = ["queued", "preparing", "uploading"].includes(item.status);
        const status = item.status === "ready" ? "已就绪" : item.status === "error" ? item.error
          : item.status === "preparing" ? "正在读取…" : item.status === "uploading" ? "正在传到电脑…" : "等待上传…";
        return <div className="m-attachment" data-status={item.status} aria-busy={busy}>
          {item.preview && !failedPreviews.has(item.id) ? <button type="button" className="m-attachment-thumb" aria-label={`预览 ${item.file.name}`} onClick={() => setPreview(item.id)}>
            <img src={item.preview} alt="" onError={() => setFailedPreviews((current) => new Set([...current, item.id]))} />
          </button> : <span className="m-attachment-thumb"><Icon size={23} strokeWidth={1.6} /></span>}
          <div className="m-attachment-copy"><strong title={item.file.name}>{item.file.name}</strong>
            <span>{fileSize(item.file.size)} · {item.file.name.includes(".") ? item.file.name.split(".").pop()?.toUpperCase() : "文件"}</span>
            <span className="m-attachment-status">{busy ? <i className="m-attachment-spinner" aria-hidden="true" /> : item.status === "error" ? <CircleAlert size={12} /> : <Check size={12} />}<MotionValue value={item.status}>{status}</MotionValue></span>
          </div>
          {item.status === "error" ? <button type="button" className="m-attachment-action" disabled={!online || disabled} onClick={() => queue.retry(item.id)} aria-label={`重试上传 ${item.file.name}`}><RotateCw size={17} /></button> : null}
          <button type="button" className="m-attachment-action" disabled={disabled} onClick={() => queue.remove(item.id)} aria-label={`移除 ${item.file.name}`}><X size={17} /></button>
        </div>;
      }}</MotionRows>
    </ul>
  </div></AnimatedFold>
    <ImageLightbox open={Boolean(selected?.preview)} source={image.current.source} label={image.current.label} path="" onClose={() => setPreview(null)} />
  </>;
}

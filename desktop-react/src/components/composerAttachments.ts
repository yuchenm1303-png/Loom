import type { Attachment } from "../types/loom";

export const MAX_COMPOSER_ATTACHMENTS = 10;
const IMAGE_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);

export function baseName(value: string): string {
  const parts = String(value || "").replaceAll("\\", "/").split("/");
  return parts.at(-1) || value;
}

export function extensionOf(value: string): string {
  const name = baseName(value);
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

export function looksLikeImage(name: string): boolean {
  return IMAGE_SUFFIXES.has(extensionOf(name));
}

export function formatAttachmentSize(size: number): string {
  if (size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function attachmentFromPath(filePath: string, size = 0): Attachment {
  const name = baseName(filePath);
  return { id: filePath, name, path: filePath, size, isImage: looksLikeImage(name) };
}

export function appendComposerAttachments(
  current: Attachment[],
  incoming: Attachment[],
): { attachments: Attachment[]; overflowed: boolean } {
  if (!incoming.length) return { attachments: current, overflowed: false };
  const known = new Set(current.map((item) => item.path));
  const next = [...current];
  let overflowed = false;
  for (const item of incoming) {
    if (known.has(item.path)) {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      continue;
    }
    if (next.length >= MAX_COMPOSER_ATTACHMENTS) {
      overflowed = true;
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      continue;
    }
    known.add(item.path);
    next.push(item);
  }
  return { attachments: next, overflowed };
}

export function releaseAttachmentPreview(item: Attachment | undefined): void {
  if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
}

export async function resolveComposerFiles(
  files: File[],
  onError?: (message: string) => void,
): Promise<Attachment[]> {
  const bridge = window.loom;
  const resolved: Attachment[] = [];
  for (const file of files) {
    const existing = bridge?.filePathFor?.(file) || "";
    if (existing) {
      resolved.push(attachmentFromPath(existing, file.size));
      continue;
    }
    if (!bridge?.stageTempFile) continue;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const staged = await bridge.stageTempFile(file.name || "pasted-file", bytes);
      if (!staged) continue;
      const entry = attachmentFromPath(staged, file.size);
      resolved.push({
        ...entry,
        name: file.name || entry.name,
        previewUrl: entry.isImage ? URL.createObjectURL(file) : undefined,
      });
    } catch {
      onError?.("Could not read one of the attachments.");
    }
  }
  return resolved;
}

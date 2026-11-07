export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
export const MAX_ATTACHMENTS = 10;
export const MAX_TOTAL_BYTES = 50 * 1024 * 1024;

export type MobileAttachment = {
  id: number;
  file: File;
  preview?: string;
  status: "queued" | "preparing" | "uploading" | "ready" | "error";
  path?: string;
  error?: string;
};

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function attachmentError(cause: unknown): string {
  const code = (cause as { code?: string })?.code;
  if (code === "HOST_OFFLINE") return "电脑已离线，连接后可重试";
  if (code === "HOST_TIMEOUT") return "传输超时，请重试";
  return cause instanceof Error ? cause.message : "上传失败，请重试";
}

/** One file in flight at a time. Removed files and disposed drafts ignore late replies. */
export class AttachmentQueue {
  private items: MobileAttachment[] = [];
  private listeners = new Set<() => void>();
  private nextId = 1;
  private processing = false;
  private disposed = false;
  constructor(private upload: (file: File, uploading: () => void) => Promise<string>) {}

  snapshot = () => this.items;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish() { for (const listener of this.listeners) listener(); }
  private update(id: number, patch: Partial<MobileAttachment>) {
    if (this.disposed) return;
    this.items = this.items.map((item) => item.id === id ? { ...item, ...patch } : item);
    this.publish();
  }

  add(files: File[]): string[] {
    if (this.disposed) return [];
    const errors: string[] = [];
    for (const file of files) {
      if (this.items.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) continue;
      const reason = file.size > MAX_ATTACHMENT_BYTES ? "单个文件不能超过 10 MB"
        : this.items.length >= MAX_ATTACHMENTS ? "每条消息最多添加 10 个附件"
        : this.items.reduce((sum, item) => sum + item.file.size, 0) + file.size > MAX_TOTAL_BYTES ? "附件总大小不能超过 50 MB" : "";
      if (reason) { errors.push(`${file.name}：${reason}`); continue; }
      const preview = /^image\/(png|jpeg|webp|gif|bmp|avif)$/.test(file.type) ? URL.createObjectURL(file) : undefined;
      this.items = [...this.items, { id: this.nextId++, file, preview, status: "queued" }];
    }
    this.publish();
    void this.pump();
    return errors;
  }

  remove(id: number) {
    const item = this.items.find((entry) => entry.id === id);
    if (item?.preview) URL.revokeObjectURL(item.preview);
    this.items = this.items.filter((entry) => entry.id !== id);
    this.publish();
  }
  retry(id: number) {
    if (this.items.find((item) => item.id === id)?.status !== "error") return;
    this.update(id, { status: "queued", error: undefined });
    void this.pump();
  }
  clear() {
    for (const item of this.items) if (item.preview) URL.revokeObjectURL(item.preview);
    this.items = [];
    this.publish();
  }
  dispose() { this.disposed = true; this.listeners.clear(); this.clear(); }

  private async pump() {
    if (this.processing || this.disposed) return;
    this.processing = true;
    try {
      let item: MobileAttachment | undefined;
      while (!this.disposed && (item = this.items.find((entry) => entry.status === "queued"))) {
        const id = item.id;
        this.update(id, { status: "preparing" });
        try {
          const path = await this.upload(item.file, () => this.update(id, { status: "uploading" }));
          if (!path) throw new Error("电脑没有保存附件，请重试");
          this.update(id, { status: "ready", path });
        } catch (cause) { this.update(id, { status: "error", error: attachmentError(cause) }); }
      }
    } finally { this.processing = false; }
  }
}

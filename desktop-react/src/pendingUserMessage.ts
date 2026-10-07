import type { TranscriptItem } from "./types/loom";

function findPending(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem | undefined {
  if (incoming.type !== "user_message" || incoming.source === "steering") return undefined;
  const text = String(incoming.text ?? "").replace(/\r\n/g, "\n").trim();
  const header = "Attached files (already saved in this workspace):";
  const marker = text.lastIndexOf(header);
  const manifest = marker >= 0 ? text.slice(marker + header.length).trim() : "";
  const hasManifest = Boolean(manifest) && manifest.split("\n").every((line) =>
    /^-\s+.+?\s+—\s+\.loom\/attachments\/.+?\s+\((?:image, shown above|read it with the file tools)(?:; extracted text: .+)?\)$/.test(line.trim()));
  const prompt = hasManifest ? text.slice(0, marker).trim() : text;
  return items.find((item) => item.type === "user_message" && item.status === "sending"
    && item.id.startsWith("pending-user-") && item.threadId === incoming.threadId
    && (!item.turnId || item.turnId === incoming.turnId)
    && (item.turnId && item.turnId === incoming.turnId
      || (Boolean(text) && String(item.text ?? "").replace(/\r\n/g, "\n").trim()
        === (item.hasAttachments && hasManifest ? prompt : text))));
}

export function preservePendingUserIdentity(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem {
  const pending = findPending(items, incoming);
  return pending ? { ...incoming, clientMessageId: pending.clientMessageId || pending.id,
    submittedAt: pending.submittedAt || incoming.submittedAt } : incoming;
}

export function reconcilePendingUserMessage(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem[] {
  const pending = findPending(items, incoming);
  return pending ? items.filter((item) => item !== pending) : items;
}

import type { TranscriptItem } from "./types/loom";

export function preservePendingUserIdentity(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem {
  if (incoming.type !== "user_message" || !incoming.text || incoming.source === "steering") return incoming;
  const pending = items.find((item) => item.status === "sending" && item.id.startsWith("pending-user-")
    && item.threadId === incoming.threadId && item.text === incoming.text);
  return pending ? { ...incoming, clientMessageId: pending.clientMessageId || pending.id,
    submittedAt: pending.submittedAt || incoming.submittedAt } : incoming;
}

export function reconcilePendingUserMessage(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem[] {
  if (incoming.type !== "user_message" || !incoming.text || incoming.source === "steering") return items;
  return items.filter((item) => !(item.status === "sending" && item.id.startsWith("pending-user-")
    && item.threadId === incoming.threadId && item.text === incoming.text));
}

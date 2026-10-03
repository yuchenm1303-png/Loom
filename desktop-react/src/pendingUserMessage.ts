import type { TranscriptItem } from "./types/loom";

export function reconcilePendingUserMessage(items: TranscriptItem[], incoming: TranscriptItem): TranscriptItem[] {
  if (incoming.type !== "user_message" || !incoming.text || incoming.source === "steering") return items;
  return items.filter((item) => !(item.status === "sending" && item.id.startsWith("pending-user-")
    && item.threadId === incoming.threadId && item.text === incoming.text));
}

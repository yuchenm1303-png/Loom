export interface ChatStickerAsset {
  key: string;
  alt: string;
  url: string;
}

export type ChatStickerSegment =
  | { kind: "text"; text: string }
  | { kind: "sticker"; asset: ChatStickerAsset };

const stickerUrl = (name: string) => new URL(`./assets/chat-stickers/generated/${name}.webp`, import.meta.url).href;

export const CHAT_STICKER_ASSETS: Record<string, ChatStickerAsset> = Object.freeze({
  joy_burst: { key: "joy_burst", alt: "开心庆祝", url: stickerUrl("joy_burst") },
  affection_hug: { key: "affection_hug", alt: "喜欢与抱抱", url: stickerUrl("affection_hug") },
  health_check: { key: "health_check", alt: "关心与照顾", url: stickerUrl("health_check") },
  thinking_soft: { key: "thinking_soft", alt: "认真思考", url: stickerUrl("thinking_soft") },
  cheer_power: { key: "cheer_power", alt: "加油鼓励", url: stickerUrl("cheer_power") },
  pout_no: { key: "pout_no", alt: "委屈或轻微不满", url: stickerUrl("pout_no") },
  comfort_friend: { key: "comfort_friend", alt: "温柔安慰", url: stickerUrl("comfort_friend") },
  red_packet_congrats: { key: "red_packet_congrats", alt: "祝贺与好运", url: stickerUrl("red_packet_congrats") },
  gift_for_you: { key: "gift_for_you", alt: "送你一份礼物", url: stickerUrl("gift_for_you") },
  sparkle_excited: { key: "sparkle_excited", alt: "惊喜与期待", url: stickerUrl("sparkle_excited") },
  soft_smile: { key: "soft_smile", alt: "友好微笑", url: stickerUrl("soft_smile") },
  got_it_point: { key: "got_it_point", alt: "收到与明白", url: stickerUrl("got_it_point") },
  heart_thanks: { key: "heart_thanks", alt: "感谢与喜欢", url: stickerUrl("heart_thanks") },
  confident_ready: { key: "confident_ready", alt: "准备好了", url: stickerUrl("confident_ready") },
  playful_wink: { key: "playful_wink", alt: "俏皮眨眼", url: stickerUrl("playful_wink") },
  confused_study: { key: "confused_study", alt: "学习困惑", url: stickerUrl("confused_study") },
  confirm_yes: { key: "confirm_yes", alt: "确认正确", url: stickerUrl("confirm_yes") },
  idea_drawing: { key: "idea_drawing", alt: "有新思路了", url: stickerUrl("idea_drawing") },
  reject_no: { key: "reject_no", alt: "明确否定", url: stickerUrl("reject_no") },
});

const INLINE_STICKER_CONTROL_PREFIX = "[[AI_LEDGER_INLINE_STICKER:";

const INLINE_STICKER_MARKER_RE = /\[\[AI_LEDGER_INLINE_STICKER:([a-z0-9_]{0,96})\]{0,2}/gi;

function stripTrailingStickerControlFragment(text: string): string {
  const value = String(text ?? "");
  const upper = value.toUpperCase();
  const prefix = INLINE_STICKER_CONTROL_PREFIX.toUpperCase();
  const markerStart = upper.lastIndexOf("[[");
  if (markerStart < 0) return value;

  const tail = upper.slice(markerStart);
  if (prefix.startsWith(tail) || tail.startsWith(prefix)) {
    return value.slice(0, markerStart);
  }
  return value;
}

export function splitInlineStickerText(text: string): ChatStickerSegment[] {
  const value = String(text ?? "");
  const segments: ChatStickerSegment[] = [];
  let cursor = 0;
  let matchedControlToken = false;
  INLINE_STICKER_MARKER_RE.lastIndex = 0;

  for (let match = INLINE_STICKER_MARKER_RE.exec(value); match; match = INLINE_STICKER_MARKER_RE.exec(value)) {
    matchedControlToken = true;
    if (match.index > cursor) {
      const prefix = stripTrailingStickerControlFragment(value.slice(cursor, match.index));
      if (prefix) {
        segments.push({ kind: "text", text: prefix });
      }
    }
    const key = String(match[1] ?? "").toLowerCase();
    const asset = CHAT_STICKER_ASSETS[key];
    if (asset) {
      segments.push({ kind: "sticker", asset });
    }
    cursor = match.index + match[0].length;
  }

  if (cursor < value.length) {
    const suffix = stripTrailingStickerControlFragment(value.slice(cursor));
    if (suffix) {
      segments.push({ kind: "text", text: suffix });
    } else if (value.slice(cursor)) {
      matchedControlToken = true;
    }
  }
  if (!segments.length && value && !matchedControlToken) {
    const clean = stripTrailingStickerControlFragment(value);
    if (clean) {
      segments.push({ kind: "text", text: clean });
    }
  }
  return segments;
}

export function isChatStickerAssetUrl(src: string | undefined): boolean {
  if (!src) return false;
  return Object.values(CHAT_STICKER_ASSETS).some((asset) => asset.url === src);
}

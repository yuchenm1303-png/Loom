export interface ChatStickerAsset {
  key: string;
  alt: string;
  url: string;
}

export type ChatStickerSegment =
  | { kind: "text"; text: string }
  | { kind: "sticker"; asset: ChatStickerAsset };

const CHAT_STICKER_ASSET_BASE_URL = "https://ai-ledger-parser.552078638.workers.dev/chat-stickers/v1";
const assetUrl = (name: string) => `${CHAT_STICKER_ASSET_BASE_URL}/${encodeURIComponent(name)}.webp`;

export const CHAT_STICKER_ASSETS: Record<string, ChatStickerAsset> = Object.freeze({
  joy_burst: { key: "joy_burst", alt: "开心庆祝", url: assetUrl("joy_burst") },
  affection_hug: { key: "affection_hug", alt: "喜欢与抱抱", url: assetUrl("affection_hug") },
  health_check: { key: "health_check", alt: "关心与照顾", url: assetUrl("health_check") },
  thinking_soft: { key: "thinking_soft", alt: "认真思考", url: assetUrl("thinking_soft") },
  cheer_power: { key: "cheer_power", alt: "加油鼓励", url: assetUrl("cheer_power") },
  pout_no: { key: "pout_no", alt: "委屈或轻微不满", url: assetUrl("pout_no") },
  comfort_friend: { key: "comfort_friend", alt: "温柔安慰", url: assetUrl("comfort_friend") },
  red_packet_congrats: { key: "red_packet_congrats", alt: "祝贺与好运", url: assetUrl("red_packet_congrats") },
  gift_for_you: { key: "gift_for_you", alt: "送你一份礼物", url: assetUrl("gift_for_you") },
  sparkle_excited: { key: "sparkle_excited", alt: "惊喜与期待", url: assetUrl("sparkle_excited") },
  soft_smile: { key: "soft_smile", alt: "友好微笑", url: assetUrl("soft_smile") },
  got_it_point: { key: "got_it_point", alt: "收到与明白", url: assetUrl("got_it_point") },
  heart_thanks: { key: "heart_thanks", alt: "感谢与喜欢", url: assetUrl("heart_thanks") },
  confident_ready: { key: "confident_ready", alt: "准备好了", url: assetUrl("confident_ready") },
  playful_wink: { key: "playful_wink", alt: "俏皮眨眼", url: assetUrl("playful_wink") },
  confused_study: { key: "confused_study", alt: "学习困惑", url: assetUrl("confused_study") },
  confirm_yes: { key: "confirm_yes", alt: "确认正确", url: assetUrl("confirm_yes") },
  idea_drawing: { key: "idea_drawing", alt: "有新思路了", url: assetUrl("idea_drawing") },
  reject_no: { key: "reject_no", alt: "明确否定", url: assetUrl("reject_no") },
});

const INLINE_STICKER_MARKER_RE = /\[\[AI_LEDGER_INLINE_STICKER:([a-z0-9_]{2,48})\]\]/gi;

export function splitInlineStickerText(text: string): ChatStickerSegment[] {
  const value = String(text ?? "");
  const segments: ChatStickerSegment[] = [];
  let cursor = 0;
  INLINE_STICKER_MARKER_RE.lastIndex = 0;

  for (let match = INLINE_STICKER_MARKER_RE.exec(value); match; match = INLINE_STICKER_MARKER_RE.exec(value)) {
    if (match.index > cursor) {
      segments.push({ kind: "text", text: value.slice(cursor, match.index) });
    }
    const key = String(match[1] ?? "").toLowerCase();
    const asset = CHAT_STICKER_ASSETS[key];
    if (asset) {
      segments.push({ kind: "sticker", asset });
    } else {
      segments.push({ kind: "text", text: match[0] });
    }
    cursor = match.index + match[0].length;
  }

  if (cursor < value.length) {
    segments.push({ kind: "text", text: value.slice(cursor) });
  }
  if (!segments.length && value) {
    segments.push({ kind: "text", text: value });
  }
  return segments;
}

"""AI Ledger inline sticker protocol v4 port for Loom.

This module intentionally preserves the current AI Ledger sticker behavior:
- the model emits candidate positions (inline markers for streams, sidecar anchors otherwise),
- frequency controls candidate density,
- intensity controls asset rotation/diversity,
- repeat/max are enforced mechanically,
- Markdown-unsafe placements and fragmented stream markers are sanitized.

Keep this module behavior-aligned with the AI Ledger worker instead of creating a
second Loom-specific sticker policy.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

INLINE_STICKER_PROTOCOL_HARD_MAX = max(1, min(128, int(os.environ.get("LOOM_INLINE_STICKER_PROTOCOL_HARD_MAX", "64"))))
INLINE_STICKER_DEFAULT_REPLY_HARD_MAX = max(
    1,
    min(
        INLINE_STICKER_PROTOCOL_HARD_MAX,
        int(os.environ.get("LOOM_INLINE_STICKER_DEFAULT_REPLY_HARD_MAX", str(INLINE_STICKER_PROTOCOL_HARD_MAX))),
    ),
)
ENABLE_CHAT_STICKERS = os.environ.get("LOOM_CHAT_STICKERS", "true").strip().casefold() != "false"

CHAT_STICKER_CATALOG: dict[str, dict[str, str]] = {
    "joy_burst": {"category": "joy", "alt": "开心庆祝", "usage": "任务完成、成功、明显好消息、热烈庆祝"},
    "affection_hug": {"category": "affection", "alt": "喜欢与抱抱", "usage": "亲密支持、喜欢、感谢陪伴、抱抱回应"},
    "health_check": {"category": "care", "alt": "关心与照顾", "usage": "身体状态、休息、健康提醒、关心用户"},
    "thinking_soft": {"category": "thinking", "alt": "认真思考", "usage": "分析、权衡、推理中、暂时需要想一想"},
    "cheer_power": {"category": "encourage", "alt": "加油鼓励", "usage": "备考、挑战、坚持、鼓励继续行动"},
    "pout_no": {"category": "displeased", "alt": "委屈或轻微不满", "usage": "轻微委屈、无奈、抗议、不开心但不严肃"},
    "comfort_friend": {"category": "comfort", "alt": "温柔安慰", "usage": "受挫、失落、压力、温柔安慰和陪伴"},
    "red_packet_congrats": {"category": "celebrate", "alt": "祝贺与好运", "usage": "节日、录取、获奖、发财、好运祝贺"},
    "gift_for_you": {"category": "gift", "alt": "送你一份礼物", "usage": "交付成果、送上方案或文件、给用户惊喜"},
    "sparkle_excited": {"category": "excited", "alt": "惊喜与期待", "usage": "发现亮点、新进展、期待、惊喜"},
    "soft_smile": {"category": "friendly", "alt": "友好微笑", "usage": "普通友好回应、轻松寒暄、柔和收尾"},
    "got_it_point": {"category": "acknowledge", "alt": "收到与明白", "usage": "收到任务、理解要求、指出关键点"},
    "heart_thanks": {"category": "thanks", "alt": "感谢与喜欢", "usage": "真诚感谢、喜欢、认可对方"},
    "confident_ready": {"category": "confident", "alt": "准备好了", "usage": "准备就绪、有把握、开始执行"},
    "playful_wink": {"category": "playful", "alt": "俏皮眨眼", "usage": "轻松玩笑、俏皮提醒、幽默互动"},
    "confused_study": {"category": "confused", "alt": "学习困惑", "usage": "题目疑惑、学习卡点、需要澄清"},
    "confirm_yes": {"category": "approval", "alt": "确认正确", "usage": "答案正确、确认可行、同意或批准"},
    "idea_drawing": {"category": "idea", "alt": "有新思路了", "usage": "提出思路、方案、灵感、推导方法"},
    "reject_no": {"category": "reject", "alt": "明确否定", "usage": "明确否定、纠正错误、拒绝不合适方案"},
}

INLINE_STICKER_VISIBLE_SUFFIX = "]]"
INLINE_STICKER_STRUCTURED_PLAN_BEGIN = "[[AI_LEDGER_STICKER_PLAN_V1_BEGIN]]"
INLINE_STICKER_STRUCTURED_PLAN_END = "[[AI_LEDGER_STICKER_PLAN_V1_END]]"
INLINE_STICKER_VISIBLE_MARKER_RE = re.compile(r"\[\[AI_LEDGER_INLINE_STICKER:([a-z0-9_]{2,48})\]\]", re.I)
INLINE_STICKER_ASSET_KEY_RE = re.compile(r"^[a-z0-9_]{2,48}$", re.I)

_STICKER_OPT_OUT_RE = re.compile(
    r"(不要|别|禁止|关闭|停用|取消).{0,10}(表情包|内联表情|聊天表情|内置表情|sticker)|(?:no|without|disable).{0,10}(?:sticker|sticker pack)",
    re.I,
)
_STICKER_OPT_IN_RE = re.compile(
    r"(请|可以|要|使用|发送|发|加|带上|开启|打开).{0,10}(表情包|内联表情|聊天表情|内置表情|sticker)|(?:use|send|enable|with).{0,10}(?:sticker|sticker pack)",
    re.I,
)
_STICKER_MENTION_RE = re.compile(r"表情包|内联表情|聊天表情|sticker|ai_ledger_inline_sticker", re.I)
_STICKER_DISPLAY_RE = re.compile(r"展示|预览|测试|列出|全部|目录|逐项|发送|看看|show|preview|test|list|catalog", re.I)


def _clamp_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return max(low, min(high, int(round(number))))


@dataclass(frozen=True, slots=True)
class StickerPreferences:
    frequency: int = 50
    intensity: int = 50
    max_per_reply: int = 0
    repeat_count: int = 1

    @classmethod
    def normalize(cls, raw: Mapping[str, Any] | None = None) -> "StickerPreferences":
        source = dict(raw or {})
        return cls(
            frequency=_clamp_int(source.get("frequency", source.get("inlineStickerFrequency")), 50, 0, 100),
            intensity=_clamp_int(source.get("intensity", source.get("inlineStickerIntensity")), 50, 0, 100),
            max_per_reply=_clamp_int(source.get("maxPerReply", source.get("inlineStickerMaxPerReply")), 0, 0, 64),
            repeat_count=_clamp_int(source.get("repeatCount", source.get("inlineStickerRepeatCount")), 1, 1, 4),
        )

    @classmethod
    def from_env(cls) -> "StickerPreferences":
        return cls.normalize({
            "frequency": os.environ.get("LOOM_INLINE_STICKER_FREQUENCY", 50),
            "intensity": os.environ.get("LOOM_INLINE_STICKER_INTENSITY", 50),
            "maxPerReply": os.environ.get("LOOM_INLINE_STICKER_MAX_PER_REPLY", 0),
            "repeatCount": os.environ.get("LOOM_INLINE_STICKER_REPEAT_COUNT", 1),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ai_ledger_chat_expression_preferences_v2",
            "frequency": self.frequency,
            "intensity": self.intensity,
            "maxPerReply": self.max_per_reply,
            "repeatCount": self.repeat_count,
        }


@dataclass(frozen=True, slots=True)
class StickerContext:
    user_text: str = ""
    assistant_history: tuple[str, ...] = ()
    streaming: bool = False
    allow_stickers: bool = True


@dataclass(frozen=True, slots=True)
class StickerResult:
    text: str
    keys: tuple[str, ...]
    diagnostics: dict[str, Any]


def frequency_spec(preferences: StickerPreferences) -> dict[str, Any]:
    value = max(0, min(100, preferences.frequency))
    if value <= 0:
        return {"tier": "off", "label": "关闭", "densityRate": 0.0, "nodeIntervalText": "关闭", "shortRule": "短回复 0 个"}
    if value < 25:
        return {"tier": "density_very_low", "label": "较少/低密度", "densityRate": 0.14, "nodeIntervalText": "约每 7-8 个自然表达节点 1 个", "shortRule": "短回复通常 0 个"}
    if value < 45:
        return {"tier": "density_low", "label": "偏少", "densityRate": 0.20, "nodeIntervalText": "约每 5-6 个自然表达节点 1 个", "shortRule": "短回复可以 0 个"}
    if value <= 55:
        return {"tier": "density_default", "label": "默认", "densityRate": 0.30, "nodeIntervalText": "约每 3-4 个自然表达节点 1 个", "shortRule": "短句不硬塞"}
    if value < 75:
        return {"tier": "density_more", "label": "较多", "densityRate": 0.42, "nodeIntervalText": "约每 2-3 个自然表达节点 1 个", "shortRule": "短回复可 1 个"}
    if value < 90:
        return {"tier": "density_high", "label": "高频", "densityRate": 0.58, "nodeIntervalText": "约每 2 个自然表达节点 1 个", "shortRule": "短回复可 1 个"}
    if value < 100:
        return {"tier": "density_very_high", "label": "很高频", "densityRate": 0.74, "nodeIntervalText": "多数自然表达节点可带 1 个", "shortRule": "短回复至少 1 个"}
    return {"tier": "density_max", "label": "拉满", "densityRate": 0.95, "nodeIntervalText": "几乎每个自然表达节点都可带 1 个", "shortRule": "短回复至少 1 个"}


def diversity_spec(preferences: StickerPreferences) -> dict[str, Any]:
    value = max(0, min(100, preferences.intensity))
    if value < 25:
        return {"tier": "diversity_low", "label": "低轮换", "minUniqueRatio": 0.35, "poolMode": "full_catalog", "instruction": "表达强度=低轮换：候选位置自然性优先，合法 assetKey 可按模型建议保留；不启用粉毛核心池。"}
    if value < 45:
        return {"tier": "diversity_restrained", "label": "轻轮换", "minUniqueRatio": 0.50, "poolMode": "full_catalog", "instruction": "表达强度=轻轮换：模型的 assetKey 仍可作为弱建议；后端在重复或缺失时按全目录机械轮换。"}
    if value <= 55:
        return {"tier": "diversity_default", "label": "默认轮换", "minUniqueRatio": 0.68, "poolMode": "full_catalog", "instruction": "表达强度=默认轮换：普通聊天不再优先粉毛常用 key；模型只需要给自然候选位置，后端按全目录机械轮换 asset。"}
    if value < 75:
        return {"tier": "diversity_more", "label": "较多轮换", "minUniqueRatio": 0.84, "poolMode": "full_catalog", "instruction": "表达强度=较多轮换：assetKey 基本只作为占位风格；后端优先选择本条回复未用过的全目录 key。"}
    if value < 90:
        return {"tier": "diversity_high", "label": "高轮换", "minUniqueRatio": 1.0, "poolMode": "full_catalog", "instruction": "表达强度=高轮换：除用户明确点名某个表情外，后端按全目录不重复轮换，不让 key 文案决定最终表情。"}
    return {"tier": "diversity_max", "label": "全库轮换", "minUniqueRatio": 1.0, "poolMode": "full_catalog", "instruction": "表达强度=全库轮换：模型只负责候选位置；表情资产完全按全目录机械轮换，尽量覆盖全部可用 key。"}


def is_catalog_or_test_request(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()[:4000]
    return bool(value and _STICKER_MENTION_RE.search(value) and _STICKER_DISPLAY_RE.search(value))


def analyze_scene(context: StickerContext, preferences: StickerPreferences) -> dict[str, Any]:
    text = str(context.user_text or "")[:4000]
    catalog_request = is_catalog_or_test_request(text)
    explicit_out = bool(_STICKER_OPT_OUT_RE.search(text))
    explicit_in = bool(_STICKER_OPT_IN_RE.search(text)) or catalog_request
    if not ENABLE_CHAT_STICKERS:
        return {"eligible": False, "allowOutput": False, "reason": "feature_disabled", "catalogOrTestRequest": catalog_request, "explicitUserOptOut": explicit_out}
    if not context.allow_stickers:
        return {"eligible": False, "allowOutput": False, "reason": "response_path_disallows_stickers", "catalogOrTestRequest": catalog_request, "explicitUserOptOut": explicit_out}
    if explicit_out and not explicit_in:
        return {"eligible": False, "allowOutput": False, "reason": "user_explicit_opt_out", "catalogOrTestRequest": catalog_request, "explicitUserOptOut": True}
    if preferences.frequency <= 0 and not catalog_request:
        return {"eligible": False, "allowOutput": False, "reason": "frequency_zero", "catalogOrTestRequest": catalog_request, "explicitUserOptOut": explicit_out}
    return {"eligible": True, "allowOutput": True, "reason": "explicit_catalog_or_test" if catalog_request else "model_candidate_positions_allowed", "catalogOrTestRequest": catalog_request, "explicitUserOptOut": explicit_out}


def effective_limit(preferences: StickerPreferences, scene: Mapping[str, Any] | None = None) -> int:
    configured = preferences.max_per_reply
    user_limit = configured if configured > 0 else INLINE_STICKER_DEFAULT_REPLY_HARD_MAX
    return min(INLINE_STICKER_PROTOCOL_HARD_MAX, max(0, user_limit))


def canonical_marker(asset_key: str) -> str:
    key = str(asset_key or "").strip().casefold()
    return f"[[AI_LEDGER_INLINE_STICKER:{key}]]" if key in CHAT_STICKER_CATALOG else ""


def extract_keys(text: str) -> list[str]:
    return [m.group(1).casefold() for m in INLINE_STICKER_VISIBLE_MARKER_RE.finditer(str(text or "")) if m.group(1).casefold() in CHAT_STICKER_CATALOG]


def _plain_reply(text: str) -> str:
    value = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", str(text or ""))
    value = re.sub(r"```[\s\S]*?```", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def natural_node_count(text: str) -> int:
    plain = _plain_reply(text)
    if not plain:
        return 0
    sentences = [part.strip() for part in re.split(r"[。！？!?；;\n]+", plain) if len(part.strip()) >= 4]
    count = max(1, len(sentences))
    for sentence in sentences:
        if len(sentence) < 36:
            continue
        clauses = [part.strip() for part in re.split(r"[，,、：:]", sentence) if len(part.strip()) >= 6]
        count += max(0, min(2, len(clauses) - 1))
    return max(1, min(8, count))


def target_location_count(preferences: StickerPreferences, reply: str, scene: Mapping[str, Any]) -> int:
    if not scene.get("eligible"):
        return 0
    limit = effective_limit(preferences, scene)
    if limit <= 0 or preferences.frequency <= 0:
        return 0
    plain_len = len(re.sub(r"\s+", "", _plain_reply(reply)))
    nodes = natural_node_count(reply)
    if plain_len < 18 or nodes <= 0:
        return 0
    rate = frequency_spec(preferences)["densityRate"]
    target = math.ceil(nodes * rate)
    if scene.get("catalogOrTestRequest"):
        target = max(target, min(len(CHAT_STICKER_CATALOG), limit))
    elif preferences.frequency < 25 and plain_len < 300 and nodes < 5:
        target = 0
    elif preferences.frequency < 45 and plain_len < 120 and nodes < 3:
        target = 0
    elif preferences.frequency <= 55 and plain_len < 70 and nodes < 2:
        target = 0
    elif preferences.frequency >= 90 and target < 1:
        target = 1
    elif preferences.frequency >= 75 and nodes >= 3 and target < 2:
        target = 2
    elif preferences.frequency >= 100 and nodes >= 4 and target < 3:
        target = 3
    capacity = max(0, limit // max(1, preferences.repeat_count))
    return max(0, min(target, nodes, capacity, limit))


def _catalog_prompt() -> str:
    return "\n".join(
        f"- {key}：{meta['alt']}；{meta['usage']}"
        for key, meta in CHAT_STICKER_CATALOG.items()
    )


def build_sticker_system_prompt(preferences: StickerPreferences, context: StickerContext) -> str:
    if not ENABLE_CHAT_STICKERS:
        return "当前 App 内置表情功能已关闭。不要输出候选侧栏，也不要输出任何 AI_LEDGER_INLINE_STICKER marker。"
    scene = analyze_scene(context, preferences)
    freq = frequency_spec(preferences)
    diversity = diversity_spec(preferences)
    limit = effective_limit(preferences, scene)
    scene_rule = (
        "当前请求允许使用内置表情候选位；你只判断自然位置，assetKey 只是弱风格建议，不决定最终数量或最终表情。"
        if scene.get("eligible")
        else f"当前请求禁用内置表情（{scene.get('reason')}），不得输出候选侧栏、候选 marker 或正式表情 marker。"
    )
    density_lines = [
        f"发送频率={freq['label']}：这是本条回复内部的候选锚点密度预算，不是跨回复概率，也不是固定数量。",
        f"目标密度={freq['nodeIntervalText']}；{freq['shortRule']}；最终硬上限 {limit} 张。",
        "自然表达节点定义：一个完整句子、一个列表项、一个短段落、一个独立说明点都算 1 个节点；标题、代码、表格、公式、引用结构不算候选锚点。",
        "生成时先写纯净正文，再根据最终正文长度和节点数提供 candidates；回复越长，候选锚点越多，并尽量分散在不同语义块。",
        "禁止把发送频率理解为‘只在本条回复末尾象征性给 1 个候选’。",
    ] if preferences.frequency > 0 else [
        "发送频率=关闭：普通回复不得输出候选侧栏，也不得输出正式表情 marker；只有用户明确要求展示或测试表情目录时例外。"
    ]
    diversity_lines = [
        diversity["instruction"],
        f"多样性档位={diversity['label']}；多个位置的目标唯一 key 比例≥{round(diversity['minUniqueRatio'] * 100)}%。",
        "表达强度只控制最终 asset 轮换方式，不控制发送数量；发送数量只由频率密度、候选位置、repeat 和上限决定。",
        "普通聊天里 assetKey 不承担严格语义，模型不要为了匹配 key 名字反复选择 soft_smile、thinking_soft、got_it_point 或 idea_drawing。",
    ]
    shared = [
        f"当前设置：发送频率 {preferences.frequency}/100（{freq['label']}），表达强度 {preferences.intensity}/100（{diversity['label']}），最终硬上限 {limit}，同位置重复 {preferences.repeat_count}。",
        "你的职责只到候选位：选择正文里自然适合挂表情的完整句子、完整短段落或完整列表项；assetKey 在普通聊天里只是占位风格，拿不准时统一写 soft_smile 即可，后端会按表达强度换图。",
        "后端负责最终数量、asset 机械轮换、去重、repeat、max、合法 key 校验和 Markdown 安全；不要把这些控制参数写成用户可见解释。",
        "候选位置要优先选择情绪承接点、轻松总结点、鼓励/确认/提醒点、分点中的完整语义项；不要机械按段落间隔凑密度，也不要集中在最后一句。",
        "候选位置禁止落在标题、表格、代码、公式、引用、列表符号、词语片段或未完句中间。",
        scene_rule,
        "本轮表情能力允许使用。发送频率是本条回复内部的密度预算，不是跨回复概率，也不是固定数量。" if scene.get("allowOutput") else f"本轮不要主动输出候选位或正式内置表情（{scene.get('reason')}）。",
        *density_lines,
        *diversity_lines,
    ]
    if context.streaming:
        mode = [
            "【最终表达协议｜流式候选 marker 版｜优先级高于普通格式偏好】",
            "当前是流式回复。为了让 App 表情随文字自然出现，不输出结构化候选侧栏 JSON。",
            "在可见正文中，你可以把 [[AI_LEDGER_INLINE_STICKER:asset_key]] 当作‘候选锚点 marker’写在自然候选位置；它不是最终数量承诺，后端会实时按用户频率/强度放行或丢弃。",
            "回复越长、自然表达节点越多，候选 marker 可以越多；短回复可以没有。不要只在末尾象征性放 1 个候选。",
            "多个候选位可以直接复用同一个合法 assetKey；位置自然性比 key 名称重要，最终丰富度由后端轮换器执行。",
            *shared,
            "输出前无声检查：正文是否正常连续流式；候选 marker 是否贴在完整语义块末尾；是否避免未完句中间突然出现；是否没有输出侧栏 JSON。",
        ]
    else:
        mode = [
            "【最终表达协议｜结构化候选侧栏版｜优先级高于普通格式偏好】",
            "普通非流式回复采用‘纯净 Markdown 正文 + 结构化候选侧栏’。可见正文里不要直接写 AI_LEDGER_INLINE_STICKER marker。",
            f"完整正文结束后，输出固定侧栏：{INLINE_STICKER_STRUCTURED_PLAN_BEGIN} + 严格 JSON + {INLINE_STICKER_STRUCTURED_PLAN_END}。后端会解析并移除侧栏，用户不会看到。",
            '侧栏 JSON 格式：{"schema":"ai_ledger_inline_sticker_plan_v1","candidates":[{"anchor":"从正文中逐字复制的完整句子、短段落或列表项","assetKey":"soft_smile","score":0.86}]}；普通聊天里 assetKey 可作为占位值，重点是 anchor 自然。',
            "score 表示该候选位置的自然程度。请给出比最终可能保留数量更多的高质量候选，让后端按频率选择位置、按强度轮换 asset；不要只给末尾 1 个。",
            "anchor 必须能在正文中逐字找到，且必须是完整语义块；如果正文有自动换行，也要选择完整句子或完整列表项，不要选择半句。",
            *shared,
            "输出前无声检查：可见正文是否完全干净；侧栏 JSON 是否严格有效；candidates 是否分散且高质量；anchor 是否逐字存在于正文。",
        ]
    identity = [
        "你拥有本 App 原生内置表情包能力；普通聊天中你只负责自然候选位置，不负责锁定最终使用哪一张图。",
        "当前请求是流式回复：使用正文内候选 marker，让后端实时放行，禁止输出结构化候选侧栏 JSON。" if context.streaming else "当前请求是非流式回复：可见正文保持纯净，最后输出结构化候选侧栏，禁止把正式 marker 混入正文。",
        "你只负责语义自然性：哪里适合挂表情。assetKey 在普通聊天里只是占位风格，后端会根据发送频率、表达强度、重复、上限和排版安全决定最终输出。",
        "目录/测试/预览请求是唯一例外：当用户明确要求展示表情库时，可以在条目后直接输出正式 marker，格式为：表情名称：[[AI_LEDGER_INLINE_STICKER:精确asset_key]]。",
        "可用 asset_key 如下。普通聊天不要把名字当成严格场景限制；它们都是可爱的视觉风格，可以按表达强度广泛轮换：\n" + _catalog_prompt(),
        "除当前模式要求的候选 marker 或候选侧栏外，不要输出其他 JSON、协议解释、未知 key、资源路径、Base64、Unicode emoji、颜文字或符号表情。",
        *mode,
    ]
    return "\n".join(identity)


def _parse_token(inner: str) -> tuple[bool, str, str]:
    raw = str(inner or "").strip()
    prefix = "AI_LEDGER_INLINE_STICKER:"
    if raw.upper().startswith(prefix):
        key = raw[len(prefix):].strip().casefold()
        if INLINE_STICKER_ASSET_KEY_RE.fullmatch(key):
            return True, key, "canonical"
        return True, key, "invalid_canonical"
    key = raw.casefold()
    if INLINE_STICKER_ASSET_KEY_RE.fullmatch(key) and key in CHAT_STICKER_CATALOG:
        return True, key, "compact_alias"
    return False, "", "plain"


def _unique(keys: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in keys:
        key = str(value or "").casefold()
        if key in CHAT_STICKER_CATALOG and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _rotation_pool() -> list[str]:
    return list(CHAT_STICKER_CATALOG)


def choose_rotated_asset(original_key: str, emitted_keys: Sequence[str], preferences: StickerPreferences, context: StickerContext) -> str:
    original = str(original_key or "").casefold()
    scene = analyze_scene(context, preferences)
    if scene.get("catalogOrTestRequest") and original in CHAT_STICKER_CATALOG:
        return original
    intensity = preferences.intensity
    chance = 0.82 if intensity < 25 else 0.52 if intensity < 45 else 0.28 if intensity <= 55 else 0.10 if intensity < 75 else 0.0
    emitted = set(_unique(emitted_keys))
    if original in CHAT_STICKER_CATALOG and original not in emitted and random.random() < chance:
        return original
    fresh = [key for key in _rotation_pool() if key not in emitted]
    repeated = [key for key in _rotation_pool() if key in emitted]
    random.shuffle(fresh)
    random.shuffle(repeated)
    ranked = [*fresh, *repeated]
    preferred = ranked[0] if ranked else original or "soft_smile"
    return preferred if preferred in CHAT_STICKER_CATALOG else "soft_smile"


def _unsafe_anchor_line(line: str) -> bool:
    value = str(line or "")
    return bool(
        not value
        or re.match(r"^#{1,6}\s+", value)
        or re.match(r"^>\s+", value)
        or re.match(r"^(```|~~~)", value)
        or re.match(r"^(\$\$|\\\[|\\\])", value)
        or re.match(r"^\|.*\|\s*$", value)
        or re.match(r"^[-:|\s]+$", value)
    )


def _semantic_anchor_score(text: str) -> float:
    value = str(text or "")
    score = 0.0
    if re.search(r"(完成|成功|可以|没问题|正确|确定|收到|明白|开始|继续|加油|放心|谢谢|感谢|不错|很好|好消息|恭喜|建议|提醒|注意)", value):
        score += 1.0
    if re.search(r"(哈哈|轻松|可爱|惊喜|喜欢|开心|期待|有意思|稳了)", value):
        score += 0.8
    if re.search(r"(但是|不过|其实|所以|因此|关键|重点)", value):
        score += 0.35
    return min(3.0, score)


def _anchor_line_score(line: str) -> float:
    value = str(line or "").strip()
    if not value or _unsafe_anchor_line(value):
        return 0.0
    is_list = bool(re.match(r"^(?:[-*•]|\d+[.)]|[（(]?\d+[）)])\s+", value))
    plain = re.sub(r"^(?:[-*•]|\d+[.)]|[（(]?\d+[）)])\s+", "", value).strip()
    plain = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", plain).strip()
    if len(plain) < 12:
        return 0.0
    score = 4.0 if is_list else 3.0
    if re.search(r"[。！？!?；;.]$", plain):
        score += 3.0
    if len(plain) >= 28:
        score += 2.0
    if len(plain) >= 70:
        score += 1.0
    score += _semantic_anchor_score(plain)
    if re.search(r"[:：]$", plain):
        score -= 3.0
    if re.search(r"(如下|包括|分为|特征|步骤|原因|背景|总结)[:：]?$", plain):
        score -= 1.0
    return max(0.0, score)


def _safe_fallback_anchors(source: str, occupied_offsets: Sequence[int]) -> list[dict[str, Any]]:
    text = str(source or "")
    anchors: list[dict[str, Any]] = []
    in_fence = False
    in_formula = False
    offset = 0
    occupied = list(occupied_offsets)
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        trimmed = line.strip()
        content_end = offset + len(line.rstrip())
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            offset += len(raw_line)
            continue
        if re.match(r"^\s*\$\$", line):
            in_formula = not in_formula
            offset += len(raw_line)
            continue
        if not in_fence and not in_formula and trimmed and not _unsafe_anchor_line(trimmed):
            clean = re.sub(r"\s+", " ", INLINE_STICKER_VISIBLE_MARKER_RE.sub("", trimmed)).strip()
            if len(clean) >= 12 and not any(abs(existing - content_end) <= 3 for existing in occupied):
                score = _anchor_line_score(trimmed)
                if score > 0:
                    anchors.append({"offset": content_end, "score": score, "text": clean[:220], "semanticRoleScore": _semantic_anchor_score(clean)})
        offset += len(raw_line)
    return anchors


def _extract_structured_plan(value: str) -> tuple[str, Any | None, dict[str, Any]]:
    source = str(value or "")
    begin = source.find(INLINE_STICKER_STRUCTURED_PLAN_BEGIN)
    if begin < 0:
        return source.strip(), None, {"structuredPlanFound": False}
    payload_start = begin + len(INLINE_STICKER_STRUCTURED_PLAN_BEGIN)
    end = source.find(INLINE_STICKER_STRUCTURED_PLAN_END, payload_start)
    if end < 0:
        return source[:begin].strip(), None, {"structuredPlanFound": True, "structuredPlanParsed": False, "structuredPlanError": "missing_end_marker"}
    payload = source[payload_start:end].strip()
    reply = (source[:begin] + source[end + len(INLINE_STICKER_STRUCTURED_PLAN_END):]).strip()
    try:
        parsed = json.loads(payload)
    except Exception as exc:
        return reply, None, {"structuredPlanFound": True, "structuredPlanParsed": False, "structuredPlanError": str(exc)[:160]}
    return reply, parsed if isinstance(parsed, dict) else None, {"structuredPlanFound": True, "structuredPlanParsed": isinstance(parsed, dict), "structuredPlanRawChars": len(payload)}


def _structured_candidates(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    raw = plan.get("candidates") or plan.get("stickerCandidates") or plan.get("inlineStickerCandidates") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        key = str(item.get("assetKey") or item.get("asset_key") or item.get("key") or item.get("id") or "").casefold()
        anchor = str(item.get("anchor") or item.get("afterText") or item.get("after_text") or item.get("text") or "").strip()
        if key not in CHAT_STICKER_CATALOG or len(anchor) < 4:
            continue
        try:
            score = float(item.get("score", item.get("confidence", 0.5)))
        except (TypeError, ValueError):
            score = 0.5
        out.append({"key": key, "anchor": anchor, "score": max(0.0, min(1.0, score)), "index": index, "mood": str(item.get("mood") or "")[:80]})
    return out


def _materialize_candidates(reply: str, plan: Any) -> list[dict[str, Any]]:
    source = str(reply or "")
    groups: list[dict[str, Any]] = []
    used: set[int] = set()
    for candidate in sorted(_structured_candidates(plan), key=lambda item: (-item["score"], item["index"])):
        anchor = candidate["anchor"]
        starts = [m.start() for m in re.finditer(re.escape(anchor), source)]
        match_offset = next((start + len(anchor) for start in starts if start + len(anchor) not in used), None)
        if match_offset is None:
            needle = re.sub(r"\s+", " ", anchor).strip()
            for line_match in re.finditer(r"[^\n]+", source):
                normalized = re.sub(r"\s+", " ", line_match.group(0)).strip()
                if normalized.endswith(needle) or needle in normalized:
                    candidate_offset = line_match.end()
                    if candidate_offset not in used:
                        match_offset = candidate_offset
                        break
        if match_offset is None:
            continue
        used.add(match_offset)
        groups.append({
            "key": candidate["key"],
            "start": match_offset,
            "end": match_offset,
            "score": candidate["score"],
            "structuredScore": candidate["score"],
            "anchorText": anchor,
            "semanticRoleScore": _semantic_anchor_score(anchor),
            "syntheticFallback": False,
        })
    return sorted(groups, key=lambda item: item["start"])


def _candidate_quality(group: Mapping[str, Any], index: int, total: int) -> float:
    model_score = max(0.0, min(1.0, float(group.get("structuredScore", group.get("score", 0.5)) or 0.5)))
    line_score = max(0.0, min(10.0, float(group.get("lineScore", _anchor_line_score(str(group.get("anchorText") or ""))) or 0.0))) / 10.0
    semantic_score = max(0.0, min(3.0, float(group.get("semanticRoleScore", _semantic_anchor_score(str(group.get("anchorText") or ""))) or 0.0))) / 3.0
    anchor_len = len(re.sub(r"\s+", "", str(group.get("anchorText") or "")))
    length_score = 0.86 if anchor_len >= 64 else 0.66 if anchor_len >= 18 else 0.25
    fallback_penalty = 0.10 if group.get("syntheticFallback") else 0.0
    edge_penalty = 0.025 if total > 2 and index in {0, total - 1} else 0.0
    return max(0.0, model_score * 0.52 + line_score * 0.16 + semantic_score * 0.24 + length_score * 0.08 - fallback_penalty - edge_penalty)


def _choose_candidate_indexes(groups: Sequence[Mapping[str, Any]], desired: int, preferences: StickerPreferences) -> list[int]:
    total = len(groups)
    desired = max(0, min(total, int(desired)))
    if desired <= 0:
        return []
    if desired >= total:
        return list(range(total))
    positions = [int(group.get("start", 0) or 0) for group in groups]
    low, high = min(positions), max(positions)
    span = max(1, high - low)
    selected: list[int] = []
    used_keys: set[str] = set()
    prefer_unique = preferences.intensity >= 55
    spread_strength = 0.22 if desired >= 3 else 0.16 if desired == 2 else 0.06
    while len(selected) < desired:
        best_index = -1
        best_score = float("-inf")
        for index, group in enumerate(groups):
            if index in selected:
                continue
            score = _candidate_quality(group, index, total)
            key = str(group.get("key") or "")
            if prefer_unique and key in used_keys:
                score -= 0.18
            if group.get("syntheticFallback"):
                score -= 0.08 if not selected else 0.02
            for prior in selected:
                distance = abs((positions[index] - positions[prior]) / span)
                score -= spread_strength * (1.35 if distance < 0.08 else 0.72 if distance < 0.16 else 0.28 if distance < 0.26 else 0.0)
            score -= index * 0.0008
            if score > best_score:
                best_score, best_index = score, index
        if best_index < 0:
            break
        selected.append(best_index)
        used_keys.add(str(groups[best_index].get("key") or ""))
    return sorted(selected)


def _normalize_markdown_placement(text: str) -> str:
    marker = r"(\[\[AI_LEDGER_INLINE_STICKER:[a-z0-9_]{2,48}\]\])"
    value = re.sub(marker + r"[ \t]+(?=(?:#{1,6}\s|>|[-*+]\s|\d+[.)]\s|```|~~~|\|))", r"\1\n", str(text or ""), flags=re.I)
    value = re.sub(r"(?<=[。！？!?；;.:：])" + marker + r"(?=(?:#{1,6}\s|>|[-*+]\s|\d+[.)]\s|```|~~~|\|))", r"\1\n", value, flags=re.I)
    return value


def _insert_groups(source: str, groups: Sequence[Mapping[str, Any]], preferences: StickerPreferences, context: StickerContext, scene: Mapping[str, Any]) -> StickerResult:
    limit = effective_limit(preferences, scene)
    target = target_location_count(preferences, source, scene)
    capacity = max(0, limit // max(1, preferences.repeat_count))
    model_groups = [dict(group) for group in groups if str(group.get("key") or "").casefold() in CHAT_STICKER_CATALOG]
    needed = max(0, min(target, capacity) - len(model_groups))
    fallback_groups: list[dict[str, Any]] = []
    if needed > 0:
        anchors = _safe_fallback_anchors(source, [int(group.get("start", 0)) for group in model_groups])
        pseudo = [{"key": "soft_smile", "start": item["offset"], "structuredScore": min(1.0, item["score"] / 10), "lineScore": item["score"], "semanticRoleScore": item["semanticRoleScore"], "anchorText": item["text"], "syntheticFallback": True} for item in anchors]
        for index in _choose_candidate_indexes(pseudo, min(needed, len(pseudo)), StickerPreferences(intensity=50)):
            item = pseudo[index]
            item["key"] = choose_rotated_asset("", [g.get("key", "") for g in [*model_groups, *fallback_groups]], preferences, context)
            fallback_groups.append(item)
    all_groups = sorted([*model_groups, *fallback_groups], key=lambda item: int(item.get("start", 0)))
    if not all_groups or not scene.get("allowOutput"):
        clean = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", source) if not scene.get("allowOutput") else source
        return StickerResult(clean.strip(), tuple(extract_keys(clean)), {
            "preferences": preferences.to_dict(), "scene": dict(scene), "effectiveLimit": limit,
            "targetLocationCount": target, "candidateLocationCount": len(all_groups), "outputMarkerCount": len(extract_keys(clean)),
        })
    desired = target
    if all_groups and desired <= 0 and preferences.frequency >= 45:
        desired = 1
    if len(all_groups) > 1 and preferences.frequency >= 75:
        desired = max(desired, min(2, len(all_groups)))
    if scene.get("catalogOrTestRequest"):
        desired = max(desired, len(all_groups))
    desired = min(desired, len(all_groups), capacity)
    selected = set(_choose_candidate_indexes(all_groups, desired, preferences))
    emitted_keys: list[str] = []
    output: list[str] = []
    cursor = 0
    marker_count = 0
    for index, group in enumerate(all_groups):
        start = max(0, min(len(source), int(group.get("start", 0))))
        end = max(start, min(len(source), int(group.get("end", start))))
        output.append(source[cursor:start])
        if index in selected:
            remaining = max(0, limit - marker_count)
            emit_count = min(preferences.repeat_count, remaining)
            if emit_count > 0:
                key = choose_rotated_asset(str(group.get("key") or ""), emitted_keys, preferences, context)
                output.append(canonical_marker(key) * emit_count)
                emitted_keys.append(key)
                marker_count += emit_count
        cursor = end
    output.append(source[cursor:])
    final = _normalize_markdown_placement("".join(output)).strip()
    return StickerResult(final, tuple(extract_keys(final)), {
        "preferences": preferences.to_dict(), "scene": dict(scene), "effectiveLimit": limit,
        "targetLocationCount": target, "candidateLocationCount": len(all_groups),
        "syntheticFallbackLocationCount": len(fallback_groups), "selectedCandidateLocationCount": len(selected),
        "selectedAssetKeysAfterRotation": _unique(emitted_keys), "outputMarkerCount": marker_count,
        "assetRotationMode": diversity_spec(preferences)["poolMode"],
    })


def _inline_marker_groups(source: str) -> tuple[str, list[dict[str, Any]]]:
    text = str(source or "")
    groups: list[dict[str, Any]] = []
    clean: list[str] = []
    cursor = 0
    clean_len = 0
    token_re = re.compile(r"\[\[([^\]]{1,96})\]\]")
    for match in token_re.finditer(text):
        prefix = text[cursor:match.start()]
        clean.append(prefix)
        clean_len += len(prefix)
        recognized, key, _source = _parse_token(match.group(1))
        if recognized:
            if key in CHAT_STICKER_CATALOG:
                if groups and groups[-1]["start"] == clean_len:
                    groups[-1]["originalRunCount"] += 1
                else:
                    groups.append({"key": key, "start": clean_len, "end": clean_len, "originalRunCount": 1, "structuredScore": 0.5, "anchorText": "", "syntheticFallback": False})
        else:
            token = match.group(0)
            clean.append(token)
            clean_len += len(token)
        cursor = match.end()
    suffix = text[cursor:]
    clean.append(suffix)
    return "".join(clean), groups


def finalize_reply(text: str, preferences: StickerPreferences, context: StickerContext) -> StickerResult:
    scene = analyze_scene(context, preferences)
    clean_reply, plan, plan_diag = _extract_structured_plan(text)
    if plan is not None:
        groups = _materialize_candidates(clean_reply, plan)
        result = _insert_groups(clean_reply, groups, preferences, context, scene)
        result.diagnostics.update(plan_diag)
        result.diagnostics["selectionMode"] = "semantic_structured_candidate_positions"
        return result
    source, groups = _inline_marker_groups(clean_reply)
    result = _insert_groups(source, groups, preferences, context, scene)
    result.diagnostics.update(plan_diag)
    result.diagnostics["selectionMode"] = "model_candidate_positions_only"
    return result


class StickerStreamSanitizer:
    """Cross-chunk port of the AI Ledger visible-marker stream sanitizer."""

    def __init__(self, preferences: StickerPreferences, context: StickerContext) -> None:
        self.preferences = preferences
        self.context = context
        self.scene = analyze_scene(context, preferences)
        self.limit = effective_limit(preferences, self.scene)
        self.pending = ""
        self.output = ""
        self.pending_key = ""
        self.pending_gap = ""
        self.model_locations = 0
        self.emitted_locations = 0
        self.marker_count = 0
        self.emitted_keys: list[str] = []
        self.candidate_count = 0
        self.invalid_count = 0
        self.structured_plan_suppressed = False

    def _budget(self) -> int:
        capacity = max(0, self.limit // max(1, self.preferences.repeat_count))
        if not self.scene.get("allowOutput") or capacity <= 0:
            return 0
        visible = f"{self.output}{self.pending_gap}{self.pending}"
        plain_len = len(re.sub(r"\s+", "", _plain_reply(visible)))
        target = target_location_count(self.preferences, visible, self.scene)
        frequency = self.preferences.frequency
        if frequency >= 45 and plain_len >= 28:
            target = max(target, 1)
        if frequency >= 75 and plain_len >= 120:
            target = max(target, 2)
        if frequency >= 90 and plain_len >= 220:
            target = max(target, 3)
        if self.model_locations <= 1 and frequency >= 45 and plain_len >= 18:
            target = max(target, 1)
        return max(0, min(capacity, target))

    def _flush_marker(self) -> str:
        if not self.pending_key:
            return ""
        self.model_locations += 1
        marker_text = ""
        if self.scene.get("allowOutput") and self.emitted_locations < self._budget() and self.marker_count < self.limit:
            remaining = max(0, self.limit - self.marker_count)
            count = min(self.preferences.repeat_count, remaining)
            if count > 0:
                key = choose_rotated_asset(self.pending_key, self.emitted_keys, self.preferences, self.context)
                marker_text = canonical_marker(key) * count
                self.emitted_keys.append(key)
                self.marker_count += count
                self.emitted_locations += 1
        trailing = self.pending_gap
        self.pending_key = ""
        self.pending_gap = ""
        value = marker_text + trailing
        self.output += value
        return value

    def _drain(self, final: bool) -> str:
        emitted = ""
        while self.pending:
            start = self.pending.find("[[")
            if start < 0:
                keep = 1 if not final and self.pending.endswith("[") else 0
                plain = self.pending[:len(self.pending) - keep] if keep else self.pending
                if plain:
                    if self.pending_key and not plain.strip() and not final:
                        self.pending_gap += plain
                    else:
                        emitted += self._flush_marker()
                        self.output += plain
                        emitted += plain
                self.pending = self.pending[-keep:] if keep else ""
                break
            if start > 0:
                prefix = self.pending[:start]
                if self.pending_key and not prefix.strip():
                    self.pending_gap += prefix
                else:
                    emitted += self._flush_marker()
                    self.output += prefix
                    emitted += prefix
                self.pending = self.pending[start:]
            if self.pending.startswith(INLINE_STICKER_STRUCTURED_PLAN_BEGIN):
                self.structured_plan_suppressed = True
                self.pending = ""
                break
            end = self.pending.find(INLINE_STICKER_VISIBLE_SUFFIX, 2)
            if end < 0:
                if final:
                    token = self.pending
                    potential = token.startswith("[[AI_LEDGER_INLINE_STICKER:") or INLINE_STICKER_STRUCTURED_PLAN_BEGIN.startswith(token)
                    if not potential:
                        emitted += self._flush_marker()
                        self.output += token
                        emitted += token
                    self.pending = ""
                break
            whole = self.pending[:end + 2]
            inner = self.pending[2:end]
            recognized, key, _source = _parse_token(inner)
            if not recognized:
                emitted += self._flush_marker()
                self.output += whole
                emitted += whole
            else:
                self.candidate_count += 1
                if key in CHAT_STICKER_CATALOG:
                    if self.scene.get("allowOutput"):
                        if self.pending_key:
                            self.pending_gap = ""
                        else:
                            self.pending_key = key
                else:
                    self.invalid_count += 1
            self.pending = self.pending[end + 2:]
        if final:
            emitted += self._flush_marker()
        return emitted

    def push(self, chunk: str) -> str:
        if self.structured_plan_suppressed:
            return ""
        self.pending += str(chunk or "")
        return self._drain(False)

    def finish(self) -> str:
        return self._drain(True)

    def value(self) -> str:
        return _normalize_markdown_placement(self.output.strip())

    def diagnostics(self) -> dict[str, Any]:
        return {
            "streamSanitizer": True,
            "candidateCount": self.candidate_count,
            "invalidCanonicalCount": self.invalid_count,
            "modelMarkerLocationCount": self.model_locations,
            "outputMarkerCount": self.marker_count,
            "emittedLocationCount": self.emitted_locations,
            "selectedAssetKeysAfterRotation": _unique(self.emitted_keys),
            "assetRotationMode": diversity_spec(self.preferences)["poolMode"],
            "streamStructuredPlanSuppressed": self.structured_plan_suppressed,
            "preferences": self.preferences.to_dict(),
            "scene": dict(self.scene),
            "effectiveLimit": self.limit,
        }


def reconcile_stream_reply(provider_result: StickerResult, streamed_reply: str) -> StickerResult:
    streamed = _normalize_markdown_placement(str(streamed_reply or "").strip())
    provider = provider_result.text
    if not streamed:
        return provider_result
    if not provider or provider == streamed:
        return StickerResult(streamed, tuple(extract_keys(streamed)), dict(provider_result.diagnostics))
    if provider.startswith(streamed):
        return provider_result
    streamed_count = len(extract_keys(streamed))
    provider_count = len(extract_keys(provider))
    if streamed_count > 0 and streamed_count >= provider_count:
        diagnostics = dict(provider_result.diagnostics)
        diagnostics["reconciledToStream"] = True
        return StickerResult(streamed, tuple(extract_keys(streamed)), diagnostics)
    return provider_result


__all__ = [
    "CHAT_STICKER_CATALOG",
    "INLINE_STICKER_STRUCTURED_PLAN_BEGIN",
    "INLINE_STICKER_STRUCTURED_PLAN_END",
    "INLINE_STICKER_VISIBLE_MARKER_RE",
    "StickerContext",
    "StickerPreferences",
    "StickerResult",
    "StickerStreamSanitizer",
    "build_sticker_system_prompt",
    "canonical_marker",
    "extract_keys",
    "finalize_reply",
    "reconcile_stream_reply",
]

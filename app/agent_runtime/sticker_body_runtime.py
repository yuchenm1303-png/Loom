from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable

from app.ai import AIMessage, MessageRole

from .contracts import AgentSession
from .stickers import (
    INLINE_STICKER_VISIBLE_MARKER_RE,
    StickerContext,
    StickerPreferences,
    StickerResult,
    _normalize_markdown_placement,
    _safe_fallback_anchors,
    analyze_scene,
    canonical_marker,
    choose_rotated_asset,
    effective_limit,
    extract_keys,
)
from .streaming_runtime import StreamingAgentRuntime

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

# Balanced coverage stays available on both rendered surfaces, but the previous
# rollout made the Thought process guarantee too eager at the default 50/100
# frequency. Keep reasoning stickers possible while reserving forced coverage
# for clearly substantive/high-frequency reasoning.
_REASONING_GUARANTEE_MIN_CHARS = 96
_REASONING_GUARANTEE_MIN_FREQUENCY = 65


def _visible_body_ranges(text: str) -> list[tuple[int, int]]:
    """Return ranges rendered as the final answer rather than Thought process."""

    source = str(text or "")
    lower = source.casefold()
    ranges: list[tuple[int, int]] = []
    cursor = 0

    while cursor < len(source):
        opening = lower.find(_THINK_OPEN, cursor)
        if opening < 0:
            if cursor < len(source):
                ranges.append((cursor, len(source)))
            break
        if opening > cursor:
            ranges.append((cursor, opening))

        reasoning_start = opening + len(_THINK_OPEN)
        closing = lower.find(_THINK_CLOSE, reasoning_start)
        if closing < 0:
            break
        cursor = closing + len(_THINK_CLOSE)

    if not source:
        return []
    if not ranges and _THINK_OPEN not in lower:
        return [(0, len(source))]
    return [(start, end) for start, end in ranges if end > start]


def _reasoning_ranges(text: str) -> list[tuple[int, int]]:
    """Return the text ranges shown inside Loom's Thought process disclosure."""

    source = str(text or "")
    lower = source.casefold()
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(source):
        opening = lower.find(_THINK_OPEN, cursor)
        if opening < 0:
            break
        reasoning_start = opening + len(_THINK_OPEN)
        closing = lower.find(_THINK_CLOSE, reasoning_start)
        if closing < 0:
            ranges.append((reasoning_start, len(source)))
            break
        ranges.append((reasoning_start, closing))
        cursor = closing + len(_THINK_CLOSE)
    return ranges


def _markers_in_ranges(text: str, ranges: Iterable[tuple[int, int]]) -> list[str]:
    source = str(text or "")
    keys: list[str] = []
    for start, end in ranges:
        keys.extend(extract_keys(source[start:end]))
    return keys


def _plain_length(text: str, ranges: Iterable[tuple[int, int]]) -> int:
    source = str(text or "")
    visible = "".join(source[start:end] for start, end in ranges)
    visible = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", visible)
    visible = re.sub(r"```[\s\S]*?```", " ", visible)
    visible = re.sub(r"~~~[\s\S]*?~~~", " ", visible)
    visible = re.sub(r"<[^>]{1,48}>", " ", visible)
    return len(re.sub(r"\s+", "", visible))


def _fallback_anchors(text: str, ranges: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
    source = str(text or "")
    anchors: list[dict[str, Any]] = []
    for start, end in ranges:
        segment = source[start:end]
        occupied = [
            match.end()
            for match in INLINE_STICKER_VISIBLE_MARKER_RE.finditer(segment)
        ]
        for anchor in _safe_fallback_anchors(segment, occupied):
            next_anchor = dict(anchor)
            next_anchor["offset"] = start + int(anchor.get("offset", 0) or 0)
            anchors.append(next_anchor)
    return anchors


def _best_balanced_anchor(text: str, ranges: Iterable[tuple[int, int]]) -> dict[str, Any] | None:
    anchors = _fallback_anchors(text, ranges)
    if not anchors:
        return None
    extent = max(1, len(str(text or "")))
    return max(
        anchors,
        key=lambda item: (
            float(item.get("score", 0) or 0)
            + float(item.get("semanticRoleScore", 0) or 0) * 0.75
            - abs((int(item.get("offset", 0) or 0) / extent) - 0.50) * 1.25
        ),
    )


def _remove_reasoning_markers(text: str, count: int) -> tuple[str, int]:
    """Free sticker capacity from reasoning when the final answer needs priority."""

    source = str(text or "")
    remaining = max(0, int(count))
    removed = 0
    for start, end in reversed(_reasoning_ranges(source)):
        if remaining <= 0:
            break
        segment = source[start:end]
        cleaned, n = INLINE_STICKER_VISIBLE_MARKER_RE.subn("", segment, count=remaining)
        if n:
            source = source[:start] + cleaned + source[end:]
            removed += n
            remaining -= n
    return source, removed


def _inject_surface_sticker(
    source: str,
    ranges: Iterable[tuple[int, int]],
    preferences: StickerPreferences,
    context: StickerContext,
    *,
    count: int,
) -> tuple[str, str, str] | None:
    anchor = _best_balanced_anchor(source, ranges)
    if anchor is None:
        return None
    offset = max(0, min(len(source), int(anchor.get("offset", 0) or 0)))
    key = choose_rotated_asset("soft_smile", extract_keys(source), preferences, context)
    marker = canonical_marker(key) * max(1, count)
    guarded = _normalize_markdown_placement(f"{source[:offset]}{marker}{source[offset:]}").strip()
    return guarded, key, str(anchor.get("text") or "")[:220]


def ensure_balanced_sticker_coverage(
    result: StickerResult,
    preferences: StickerPreferences,
    context: StickerContext,
) -> StickerResult:
    """Keep stickers distributed across Thought process and final answer.

    The base sticker engine owns the dynamic quantity. This postcondition keeps
    the final answer from going empty, while Thought process remains eligible
    without automatically adding a second sticker to ordinary/default-density
    replies. Forced reasoning coverage is reserved for longer reasoning at a
    higher frequency so the overall visual density stays restrained.
    """

    scene = analyze_scene(context, preferences)
    source = str(result.text or "")
    diagnostics = dict(result.diagnostics)
    limit = effective_limit(preferences, scene)

    if (
        not scene.get("allowOutput")
        or preferences.frequency <= 0
        or limit <= 0
        or scene.get("catalogOrTestRequest")
    ):
        return StickerResult(source.strip(), tuple(extract_keys(source)), diagnostics)

    repeat = max(1, preferences.repeat_count)
    body_ranges = _visible_body_ranges(source)
    reasoning_ranges = _reasoning_ranges(source)
    body_len = _plain_length(source, body_ranges)
    reasoning_len = _plain_length(source, reasoning_ranges)

    diagnostics.update({
        "stickerCoverageMode": "balanced_reasoning_and_answer_restrained",
        "visibleBodyStickerCount": len(_markers_in_ranges(source, body_ranges)),
        "reasoningStickerCount": len(_markers_in_ranges(source, reasoning_ranges)),
        "reasoningGuaranteeMinChars": _REASONING_GUARANTEE_MIN_CHARS,
        "reasoningGuaranteeMinFrequency": _REASONING_GUARANTEE_MIN_FREQUENCY,
    })

    # The final answer remains the hard priority. If a very small explicit max
    # has already been consumed by Thought process, move enough capacity back to
    # the answer rather than letting the user-visible body end up empty.
    if body_len >= 18 and not _markers_in_ranges(source, body_ranges):
        current = len(extract_keys(source))
        needed = repeat
        if current + needed > limit:
            source, removed = _remove_reasoning_markers(source, current + needed - limit)
            if removed:
                diagnostics["reasoningMarkersRebalancedToAnswer"] = removed
        body_ranges = _visible_body_ranges(source)
        if len(extract_keys(source)) + needed <= limit:
            inserted = _inject_surface_sticker(
                source,
                body_ranges,
                preferences,
                context,
                count=min(repeat, max(1, limit - len(extract_keys(source)))),
            )
            if inserted is not None:
                source, key, anchor = inserted
                diagnostics.update({
                    "bodyStickerGuaranteeApplied": True,
                    "bodyStickerGuaranteeAssetKey": key,
                    "bodyStickerGuaranteeAnchor": anchor,
                })

    # Thought process stays a first-class sticker surface, but default-density
    # replies no longer receive a mechanically forced extra sticker. The model
    # may still place a natural reasoning candidate; this pass only guarantees
    # one for clearly long/high-frequency reasoning.
    reasoning_ranges = _reasoning_ranges(source)
    reasoning_should_be_guaranteed = (
        preferences.frequency >= _REASONING_GUARANTEE_MIN_FREQUENCY
        and reasoning_len >= _REASONING_GUARANTEE_MIN_CHARS
    )
    diagnostics["reasoningGuaranteeEligible"] = reasoning_should_be_guaranteed
    if reasoning_should_be_guaranteed and not _markers_in_ranges(source, reasoning_ranges):
        remaining = limit - len(extract_keys(source))
        if remaining >= 1:
            inserted = _inject_surface_sticker(
                source,
                reasoning_ranges,
                preferences,
                context,
                count=min(repeat, remaining),
            )
            if inserted is not None:
                source, key, anchor = inserted
                diagnostics.update({
                    "reasoningStickerGuaranteeApplied": True,
                    "reasoningStickerGuaranteeAssetKey": key,
                    "reasoningStickerGuaranteeAnchor": anchor,
                })

    body_ranges = _visible_body_ranges(source)
    reasoning_ranges = _reasoning_ranges(source)
    diagnostics["visibleBodyStickerCount"] = len(_markers_in_ranges(source, body_ranges))
    diagnostics["reasoningStickerCount"] = len(_markers_in_ranges(source, reasoning_ranges))
    diagnostics["outputMarkerCountAfterCoverage"] = len(extract_keys(source))
    return StickerResult(source.strip(), tuple(extract_keys(source)), diagnostics)


# Backwards-compatible name for callers/tests from the previous body-first
# rollout. Its semantics are now balanced rather than reasoning-suppressing.
def ensure_visible_body_sticker(
    result: StickerResult,
    preferences: StickerPreferences,
    context: StickerContext,
) -> StickerResult:
    return ensure_balanced_sticker_coverage(result, preferences, context)


class BalancedStickerStreamingAgentRuntime(StreamingAgentRuntime):
    """Production runtime with dynamic, evenly distributed rendered stickers."""

    def _prepare_model_request(self, session, step, token):
        messages, extra = super()._prepare_model_request(session, step, token)
        guard_prompt = (
            "【Loom 表情均匀分布规则｜高优先级】\n"
            "表情候选可以出现在 <think>...</think> Thought process，也可以出现在最终正文；两部分都是可渲染区域。\n"
            "整体密度保持克制：比密集版少一些，优先留白，不要为了覆盖每一段而机械塞表情。\n"
            "数量仍然跟随发送频率、回复长度和自然表达节点动态增长；短到中等回复通常只需要 1 个自然位置，明显长回复再逐步增加。\n"
            "有多个候选时尽量均匀覆盖整条输出的前段、中段、后段，不要集中塞在开头、结尾或同一段。\n"
            "最终正文优先保证：只要允许表情且正文形成完整自然表达节点，正文至少给 1 个自然候选。\n"
            "Thought process 可以有表情，但默认频率下不要求每段思考都放；只有思考明显较长、位置很自然或发送频率较高时再提供候选。\n"
            "标题、代码、表格、公式和未完句中间仍然不要放候选。"
        )
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role is MessageRole.SYSTEM:
            insert_at += 1
        messages.insert(
            insert_at,
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_balanced_sticker_distribution",
                content=guard_prompt,
            ),
        )
        merged_extra = dict(extra)
        merged_extra["sticker_coverage"] = "balanced_reasoning_and_answer_restrained_v3"
        return messages, merged_extra

    def _finalize_sticker_model_text(
        self,
        session: AgentSession,
        *,
        step_id: str,
        raw_text: str,
    ) -> StickerResult:
        result = super()._finalize_sticker_model_text(
            session,
            step_id=step_id,
            raw_text=raw_text,
        )
        with self._sticker_guard:
            preferences = self._sticker_preferences
        context = self._sticker_context_for_session(
            session,
            streaming=self.provider_streaming_enabled,
        )
        guarded = ensure_balanced_sticker_coverage(result, preferences, context)

        with self._sticker_guard:
            key = (session.session_id, session.current_turn_id, step_id)
            diagnostics = dict(guarded.diagnostics)
            self._sticker_diagnostics[key] = diagnostics
            self._sticker_final_by_turn[(session.session_id, session.current_turn_id)] = guarded.text
        return replace(guarded, diagnostics=diagnostics)


# Keep the old class symbol import-compatible while production switches to the
# clearer balanced name.
BodyFirstStickerStreamingAgentRuntime = BalancedStickerStreamingAgentRuntime


__all__ = [
    "BalancedStickerStreamingAgentRuntime",
    "BodyFirstStickerStreamingAgentRuntime",
    "ensure_balanced_sticker_coverage",
    "ensure_visible_body_sticker",
]

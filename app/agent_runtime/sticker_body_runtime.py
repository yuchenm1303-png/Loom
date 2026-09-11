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
    """Keep stickers distributed across both Thought process and final answer.

    The normal sticker engine already chooses a dynamic quantity from reply
    length, natural nodes and the frequency setting. This postcondition only
    prevents one rendered surface from monopolizing those stickers: the final
    answer has first priority, then substantive Thought process receives one
    when capacity remains. Existing reasoning stickers are preserved instead of
    being stripped.
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
        "stickerCoverageMode": "balanced_reasoning_and_answer",
        "visibleBodyStickerCount": len(_markers_in_ranges(source, body_ranges)),
        "reasoningStickerCount": len(_markers_in_ranges(source, reasoning_ranges)),
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

    # Thought process is now a first-class rendered surface too. It may carry
    # stickers naturally, but it never steals the last available slot from the
    # final answer because the answer pass above runs first.
    reasoning_ranges = _reasoning_ranges(source)
    if reasoning_len >= 18 and not _markers_in_ranges(source, reasoning_ranges):
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
            "数量不要固定成某个数字，而要跟随发送频率、回复长度和自然表达节点动态增长。\n"
            "有多个候选时必须尽量均匀覆盖整条输出的前段、中段、后段，不要集中塞在开头、结尾或同一段。\n"
            "最终正文优先保证：只要允许表情且正文形成完整自然表达节点，正文至少给 1 个自然候选；"
            "Thought process 足够长时也应自然提供候选。\n"
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
        merged_extra["sticker_coverage"] = "balanced_reasoning_and_answer_v2"
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

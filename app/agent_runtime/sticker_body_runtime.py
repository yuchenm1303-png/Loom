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
    """Return source ranges rendered as the user-visible answer, not reasoning.

    Transcript.tsx treats text before <think> plus text after </think> as the
    answer. The sticker layer used to score the whole provider payload, which
    meant a valid sticker could be spent inside the collapsed Thought process
    while the actual answer body received none.
    """

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
            # An unfinished reasoning stream has no visible answer suffix yet.
            break
        cursor = closing + len(_THINK_CLOSE)

    if not source:
        return []
    if not ranges and _THINK_OPEN not in lower:
        return [(0, len(source))]
    return [(start, end) for start, end in ranges if end > start]


def _reasoning_ranges(text: str) -> list[tuple[int, int]]:
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


def _strip_reasoning_markers(text: str) -> tuple[str, int]:
    """Drop sticker control markers that landed inside hidden reasoning."""

    source = str(text or "")
    ranges = _reasoning_ranges(source)
    if not ranges:
        return source, 0

    output: list[str] = []
    cursor = 0
    removed = 0
    for start, end in ranges:
        output.append(source[cursor:start])
        reasoning = source[start:end]
        cleaned, count = INLINE_STICKER_VISIBLE_MARKER_RE.subn("", reasoning)
        output.append(cleaned)
        removed += count
        cursor = end
    output.append(source[cursor:])
    return "".join(output), removed


def _markers_in_ranges(text: str, ranges: Iterable[tuple[int, int]]) -> list[str]:
    keys: list[str] = []
    source = str(text or "")
    for start, end in ranges:
        keys.extend(extract_keys(source[start:end]))
    return keys


def _body_plain_length(text: str, ranges: Iterable[tuple[int, int]]) -> int:
    source = str(text or "")
    visible = "".join(source[start:end] for start, end in ranges)
    visible = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", visible)
    visible = re.sub(r"```[\s\S]*?```", " ", visible)
    visible = re.sub(r"~~~[\s\S]*?~~~", " ", visible)
    visible = re.sub(r"<[^>]{1,48}>", " ", visible)
    return len(re.sub(r"\s+", "", visible))


def _body_fallback_anchors(text: str, ranges: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
    source = str(text or "")
    anchors: list[dict[str, Any]] = []
    for start, end in ranges:
        segment = source[start:end]
        for anchor in _safe_fallback_anchors(segment, []):
            next_anchor = dict(anchor)
            next_anchor["offset"] = start + int(anchor.get("offset", 0) or 0)
            anchors.append(next_anchor)
    return anchors


def ensure_visible_body_sticker(
    result: StickerResult,
    preferences: StickerPreferences,
    context: StickerContext,
) -> StickerResult:
    """Enforce the visible-answer sticker postcondition.

    For every eligible non-empty answer with stickers enabled, the visible body
    gets at least one sticker whenever a Markdown-safe body anchor exists. This
    is deliberately a backend postcondition rather than a model-only prompt:
    providers may ignore candidate instructions, and reasoning markers must not
    count toward the answer-body guarantee.
    """

    scene = analyze_scene(context, preferences)
    source, removed_reasoning_markers = _strip_reasoning_markers(result.text)
    diagnostics = dict(result.diagnostics)
    if removed_reasoning_markers:
        diagnostics["reasoningStickerMarkersSuppressed"] = removed_reasoning_markers

    ranges = _visible_body_ranges(source)
    body_keys = _markers_in_ranges(source, ranges)
    diagnostics["visibleBodyStickerCount"] = len(body_keys)

    if (
        body_keys
        or not scene.get("allowOutput")
        or preferences.frequency <= 0
        or effective_limit(preferences, scene) <= 0
        or _body_plain_length(source, ranges) < 18
    ):
        keys = tuple(extract_keys(source))
        return StickerResult(source.strip(), keys, diagnostics)

    anchors = _body_fallback_anchors(source, ranges)
    if not anchors:
        diagnostics["bodyStickerGuaranteeSkipped"] = "no_safe_visible_body_anchor"
        keys = tuple(extract_keys(source))
        return StickerResult(source.strip(), keys, diagnostics)

    # Prefer a strong natural expression near the middle of the visible answer.
    # This keeps the fallback in the body rather than looking like a signature
    # appended after the final line.
    extent = max(1, len(source))
    anchor = max(
        anchors,
        key=lambda item: (
            float(item.get("score", 0) or 0)
            + float(item.get("semanticRoleScore", 0) or 0) * 0.8
            - abs((int(item.get("offset", 0) or 0) / extent) - 0.52) * 1.4
        ),
    )
    offset = max(0, min(len(source), int(anchor.get("offset", 0) or 0)))
    limit = effective_limit(preferences, scene)
    count = max(1, min(preferences.repeat_count, limit))
    key = choose_rotated_asset("soft_smile", extract_keys(source), preferences, context)
    marker = canonical_marker(key) * count
    guarded = _normalize_markdown_placement(f"{source[:offset]}{marker}{source[offset:]}").strip()

    diagnostics.update({
        "bodyStickerGuaranteeApplied": True,
        "bodyStickerGuaranteeAssetKey": key,
        "bodyStickerGuaranteeAnchor": str(anchor.get("text") or "")[:220],
        "visibleBodyStickerCount": count,
    })
    return StickerResult(guarded, tuple(extract_keys(guarded)), diagnostics)


class BodyFirstStickerStreamingAgentRuntime(StreamingAgentRuntime):
    """Production runtime that gives the visible answer priority over reasoning."""

    def _prepare_model_request(self, session, step, token):
        messages, extra = super()._prepare_model_request(session, step, token)
        guard_prompt = (
            "【Loom 正文表情优先规则】\n"
            "表情候选只能服务用户可见的最终正文，绝不要把表情候选放进 <think>...</think> 思考内容。\n"
            "只要本轮允许表情，并且最终正文形成至少一个完整自然表达节点，最终正文必须至少提供 1 个自然候选位置；"
            "思考过程里的候选不计数。正文优先，其次才考虑额外密度和分布。"
        )
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role is MessageRole.SYSTEM:
            insert_at += 1
        messages.insert(
            insert_at,
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_visible_body_sticker_guard",
                content=guard_prompt,
            ),
        )
        merged_extra = dict(extra)
        merged_extra["sticker_body_guard"] = "visible_answer_minimum_one_v1"
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
        guarded = ensure_visible_body_sticker(result, preferences, context)

        # The parent caches its pre-guard finalization for TURN_COMPLETED. Keep
        # that durable boundary aligned with the item/completed text returned by
        # this override so a later turn event cannot restore the unguarded body.
        with self._sticker_guard:
            key = (session.session_id, session.current_turn_id, step_id)
            diagnostics = dict(guarded.diagnostics)
            self._sticker_diagnostics[key] = diagnostics
            self._sticker_final_by_turn[(session.session_id, session.current_turn_id)] = guarded.text
        return replace(guarded, diagnostics=diagnostics)


__all__ = [
    "BodyFirstStickerStreamingAgentRuntime",
    "ensure_visible_body_sticker",
]

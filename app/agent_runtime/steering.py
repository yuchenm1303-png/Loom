from __future__ import annotations

"""Durable steering records and receipts; execution belongs to AgentRuntime."""
import hashlib
import json
import uuid
from typing import Any

_STEERING_PROMPT_MARKER = "LOOM_LIVE_STEERING_CONTRACT"
_STEERING_PROMPT = (
    f"[{_STEERING_PROMPT_MARKER}] The user may provide new guidance while this turn is still running. "
    "Apply the latest guidance to work that has not yet been executed. Treat completed tool results and "
    "other already-observed effects as facts: do not repeat them, pretend they did not happen, or undo "
    "them unless the user explicitly asks you to."
)


def _content_record(content: Any) -> Any:
    from app.ai import ImagePart, TextPart

    if isinstance(content, str):
        return content
    output: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, TextPart):
            output.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            output.append({"type": "image", "image_url": part.image_url, "detail": part.detail})
        else:
            raise TypeError("unsupported steering content part")
    return output


def _content_from_record(raw: Any) -> Any:
    from app.ai import ImagePart, TextPart

    if not isinstance(raw, list):
        return str(raw or "")
    parts = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid steering content")
        kind = str(item.get("type") or "")
        if kind == "text":
            parts.append(TextPart(str(item.get("text") or "")))
        elif kind == "image":
            parts.append(ImagePart(str(item.get("image_url") or ""), detail=str(item.get("detail") or "auto")))
        else:
            raise ValueError(f"unsupported steering content type: {kind!r}")
    return tuple(parts)


def _content_digest(record: Any) -> str:
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _input_id(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 128:
        raise ValueError("clientInputId must be at most 128 characters")
    return text or uuid.uuid4().hex


def _submit_once(
    store: Any,
    *,
    session_id: str,
    turn_id: str,
    text: str,
    input_id: str,
    content_record: Any | None = None,
    content_digest: str = "",
) -> tuple[str, bool, str]:
    """Durably enqueue one steering input with its original submission time."""

    from app.agent_runtime.journal import atomic_json, session_lock
    from app.agent_runtime.storage import utc_now

    directory = store.session_dir(session_id)
    with session_lock(directory):
        path = directory / "steering.json"
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        for item in items:
            if str(item.get("id") or "") != input_id:
                continue
            same_turn = str(item.get("turn_id") or "") == turn_id
            same_text = str(item.get("text") or "") == text
            stored_digest = str(item.get("content_digest") or "")
            same_content = not content_digest or not stored_digest or stored_digest == content_digest
            if not same_turn or not same_text or not same_content:
                raise ValueError("clientInputId was already used for different steering input")
            submitted_at = str(item.get("submitted_at") or "").strip()
            if not submitted_at:
                submitted_at = utc_now()
                item["submitted_at"] = submitted_at
                atomic_json(path, items)
            return input_id, True, submitted_at
        if len(items) >= 100 or len(text) > 100_000:
            raise ValueError("steering inbox limit reached")
        submitted_at = utc_now()
        items.append({
            "id": input_id,
            "turn_id": turn_id,
            "text": text,
            "content": content_record if content_record is not None else text,
            "content_digest": content_digest,
            "submitted_at": submitted_at,
        })
        atomic_json(path, items)
    return input_id, False, submitted_at


def _receipt(
    *,
    input_id: str,
    duplicate: bool,
    delivery: str,
    submitted_at: str | None = None,
    resume_required: bool = False,
    applied: bool = False,
) -> dict[str, Any]:
    return {
        "accepted": True,
        "input_id": input_id,
        "duplicate": bool(duplicate),
        "delivery": delivery,
        "submittedAt": str(submitted_at or "") or None,
        "resume_required": bool(resume_required),
        "applied": bool(applied),
    }


def _consumed_duplicate(
    runtime: Any,
    session: Any,
    *,
    turn_id: str,
    text: str,
    input_id: str,
    content_digest: str = "",
) -> dict[str, Any] | None:
    """Return idempotent success for guidance that is already in history.

    ``steering_ids`` is session-wide, so the durable USER_MESSAGE event is the
    authority for which turn/text owns an id. This lets a lost RPC response be
    retried even after queue auto-drain has advanced ``current_turn_id`` while
    still rejecting accidental reuse of that key for different guidance.
    """

    if input_id not in session.steering_ids:
        return None
    for event in reversed(runtime.store.events(session.session_id)):
        data = event.data
        if event.kind.value != "user_message":
            continue
        if str(data.get("source") or "") != "steering":
            continue
        if str(data.get("input_id") or "") != input_id:
            continue
        event_digest = str(data.get("content_digest") or "")
        if (
            event.turn_id != turn_id
            or str(data.get("text") or "") != text
            or (content_digest and event_digest and content_digest != event_digest)
        ):
            raise ValueError("clientInputId was already used for different steering input")
        return _receipt(
            input_id=input_id,
            duplicate=True,
            delivery="applied",
            submitted_at=str(data.get("submitted_at") or event.created_at or "") or None,
            applied=True,
        )
    raise ValueError("clientInputId was already used for different steering input")

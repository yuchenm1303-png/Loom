from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def approval_fingerprint(thread_id: str, approval: dict[str, Any]) -> str:
    """Bind an approval card to the exact pending Loom request it displayed."""

    payload = {
        "threadId": str(thread_id or "").strip(),
        "turnId": str(approval.get("turnId") or "").strip(),
        "requestId": str(approval.get("requestId") or "").strip(),
        "callId": str(approval.get("callId") or "").strip(),
        "toolName": str(approval.get("toolName") or "").strip(),
        "arguments": approval.get("arguments") or {},
        "approvalStage": str(approval.get("approvalStage") or "").strip(),
    }
    if not payload["threadId"] or not payload["turnId"] or not payload["callId"]:
        raise ValueError("approval fingerprint requires threadId, turnId, and callId")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def approval_fingerprint_matches(
    thread_id: str,
    approval: dict[str, Any],
    candidate: str,
) -> bool:
    expected = approval_fingerprint(thread_id, approval)
    return hmac.compare_digest(expected, str(candidate or "").strip())


__all__ = ["approval_fingerprint", "approval_fingerprint_matches"]

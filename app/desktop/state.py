"""Client-side thread state for the desktop transcript.

The App Server already exposes a fully ordered, typed timeline through
``turns[].items[]``: user messages, assistant messages, tool calls, approvals,
managed processes, file edits and turn errors. This module turns that timeline
-- plus live ``item/*`` notifications -- into a stable list of transcript
entries the view can reconcile by key.

Nothing here imports Qt, so the reconciliation rules are testable on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.desktop import format as fmt


OPTIMISTIC_USER_KEY = "optimistic:user"

_ENTRY_KINDS = {
    "user_message": "user",
    "assistant_message": "assistant",
    "tool_call": "tool",
    "process": "process",
    "file_edit": "diff",
    "error": "error",
}

# Approvals are surfaced by the dedicated approval card and by the owning
# tool_call item's status, so they never become their own transcript entry.
_SKIPPED_ITEM_TYPES = {"approval"}


@dataclass(slots=True)
class TranscriptEntry:
    """One renderable row of the conversation."""

    key: str
    kind: str
    text: str = ""
    streaming: bool = False
    item: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return fmt.text(self.item.get("status"))

    def signature(self) -> tuple[Any, ...]:
        """Cheap change token: if this is equal, the widget needs no update."""
        return (
            self.kind,
            self.text,
            self.streaming,
            self.status,
            fmt.text(self.item.get("toolName")),
            fmt.text(self.item.get("content"))[-2000:],
            fmt.text(self.item.get("stdout"))[-2000:],
            fmt.text(self.item.get("stderr"))[-2000:],
            fmt.text(self.item.get("diff"))[-2000:],
            tuple(self.item.get("paths") or ()),
            bool(self.item.get("ok")),
        )


class ThreadState:
    """Ordered transcript for one durable thread.

    Items are keyed by their App Server item id, so a snapshot refresh and a
    live notification for the same item converge instead of duplicating.
    """

    def __init__(self) -> None:
        self.thread_id = ""
        self.thread: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {}
        self.pending_approval: dict[str, Any] | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._streaming: set[str] = set()
        self._optimistic_user = ""

    # ---- reads -----------------------------------------------------------

    @property
    def workspace(self) -> str:
        return fmt.text(self.thread.get("workspace"))

    @property
    def status(self) -> str:
        return fmt.text(self.thread.get("status")) or "idle"

    @property
    def title(self) -> str:
        return fmt.text(self.thread.get("title")).strip()

    @property
    def archived(self) -> bool:
        return bool(self.thread.get("archived"))

    @property
    def total_tokens(self) -> int:
        usage = self.thread.get("usage")
        if not isinstance(usage, dict):
            return 0
        try:
            return int(usage.get("totalTokens") or 0)
        except (TypeError, ValueError):
            return 0

    def entries(self) -> list[TranscriptEntry]:
        rows: list[TranscriptEntry] = []
        for key in self._order:
            item = self._items.get(key)
            if item is None:
                continue
            entry = self._entry(key, item)
            if entry is not None:
                rows.append(entry)
        if self._optimistic_user:
            rows.append(
                TranscriptEntry(
                    key=OPTIMISTIC_USER_KEY,
                    kind="user",
                    text=self._optimistic_user,
                    streaming=True,
                )
            )
        return rows

    def _entry(self, key: str, item: dict[str, Any]) -> TranscriptEntry | None:
        item_type = fmt.text(item.get("type"))
        kind = _ENTRY_KINDS.get(item_type)
        if kind is None:
            return None
        body = fmt.text(item.get("text"))
        if kind in {"user", "assistant"} and not body.strip() and key not in self._streaming:
            return None
        return TranscriptEntry(
            key=key,
            kind=kind,
            text=body,
            streaming=key in self._streaming,
            item=item,
        )

    def items_of_type(self, item_type: str) -> list[dict[str, Any]]:
        return [
            item
            for key in self._order
            if (item := self._items.get(key)) is not None
            and fmt.text(item.get("type")) == item_type
        ]

    def tool_items(self, predicate) -> list[dict[str, Any]]:
        return [item for item in self.items_of_type("tool_call") if predicate(item)]

    # ---- writes ----------------------------------------------------------

    def reset(self) -> None:
        self.thread_id = ""
        self.thread = {}
        self.snapshot = {}
        self.pending_approval = None
        self._items.clear()
        self._order.clear()
        self._streaming.clear()
        self._optimistic_user = ""

    def apply_snapshot(self, snapshot: dict[str, Any]) -> bool:
        """Replace durable state. Returns False when the payload has no thread."""
        thread = snapshot.get("thread")
        if not isinstance(thread, dict):
            return False
        thread_id = fmt.text(thread.get("id")).strip()
        if not thread_id:
            return False

        switching = thread_id != self.thread_id
        self.thread_id = thread_id
        self.thread = dict(thread)
        self.snapshot = snapshot
        approval = snapshot.get("pendingApproval")
        self.pending_approval = approval if isinstance(approval, dict) and approval.get("callId") else None

        if switching:
            self._streaming.clear()
            self._optimistic_user = ""

        durable = list(self._snapshot_items(snapshot))
        durable_keys = {fmt.text(item.get("id")) for item in durable}

        # Keep live-only items that the snapshot has not committed yet, so an
        # in-flight turn does not visibly collapse on every refresh.
        preserved = [
            key
            for key in self._order
            if key not in durable_keys and key in self._streaming
        ]
        preserved_items = {key: self._items[key] for key in preserved if key in self._items}

        self._items = {fmt.text(item.get("id")): item for item in durable}
        self._order = [fmt.text(item.get("id")) for item in durable]
        for key in preserved:
            item = preserved_items.get(key)
            if item is not None:
                self._items[key] = item
                self._order.append(key)

        self._streaming &= set(self._order)
        if any(fmt.text(item.get("type")) == "user_message" for item in durable):
            self._optimistic_user = ""
        return True

    @classmethod
    def _snapshot_items(cls, snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Ordered durable items, with a plain-message fallback.

        ``turns[].items[]`` is the richer source and normally already contains
        the conversation itself. A server that reports only tool/process items
        there, or one whose events were pruned, still hands back ``messages``;
        those are prepended so the conversation never renders empty.
        """
        timeline: list[dict[str, Any]] = []
        has_messages = False
        for turn in snapshot.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            for item in turn.get("items") or []:
                if not isinstance(item, dict) or not fmt.text(item.get("id")):
                    continue
                item_type = fmt.text(item.get("type"))
                if item_type in _SKIPPED_ITEM_TYPES:
                    continue
                if item_type in {"user_message", "assistant_message"}:
                    has_messages = True
                timeline.append(item)

        if not has_messages:
            yield from cls._message_items(snapshot)
        yield from timeline

    @staticmethod
    def _message_items(snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for index, message in enumerate(snapshot.get("messages") or []):
            if not isinstance(message, dict):
                continue
            role = fmt.text(message.get("role"))
            content = fmt.text(message.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            yield {
                "id": f"message:{index}",
                "type": "user_message" if role == "user" else "assistant_message",
                "status": "completed",
                "text": content,
            }

    def set_optimistic_user(self, value: str) -> None:
        self._optimistic_user = fmt.text(value).strip()

    def clear_optimistic_user(self) -> None:
        self._optimistic_user = ""

    def upsert_item(self, item: Any, *, streaming: bool | None = None) -> str:
        """Insert or merge one live item. Returns its key ("" when unusable)."""
        if not isinstance(item, dict):
            return ""
        key = fmt.text(item.get("id"))
        if not key:
            return ""
        item_type = fmt.text(item.get("type"))
        if item_type in _SKIPPED_ITEM_TYPES:
            return ""

        existing = self._items.get(key)
        if existing is None:
            self._items[key] = dict(item)
            self._order.append(key)
        else:
            merged = dict(existing)
            for field_name, value in item.items():
                # A completion frame legitimately carries empty text for items
                # whose body only ever arrived as deltas; do not erase it.
                if field_name == "text" and not fmt.text(value) and fmt.text(merged.get("text")):
                    continue
                merged[field_name] = value
            self._items[key] = merged

        if item_type == "user_message":
            self._optimistic_user = ""
        if streaming is True:
            self._streaming.add(key)
        elif streaming is False:
            self._streaming.discard(key)
        return key

    def append_text_delta(self, item_id: Any, chunk: Any) -> str:
        """Append streamed assistant text. Returns the affected key."""
        key = fmt.text(item_id)
        chunk = fmt.text(chunk)
        if not key or not chunk:
            return ""
        item = self._items.get(key)
        if item is None:
            item = {"id": key, "type": "assistant_message", "status": "running", "text": ""}
            self._items[key] = item
            self._order.append(key)
        item["text"] = fmt.text(item.get("text")) + chunk
        self._streaming.add(key)
        return key

    def append_process_output(self, item_id: Any, *, stdout: str = "", stderr: str = "") -> str:
        key = fmt.text(item_id)
        if not key or not (stdout or stderr):
            return ""
        item = self._items.get(key)
        if item is None:
            item = {"id": key, "type": "process", "status": "running"}
            self._items[key] = item
            self._order.append(key)
        if stdout:
            item["stdout"] = fmt.text(item.get("stdout")) + stdout
        if stderr:
            item["stderr"] = fmt.text(item.get("stderr")) + stderr
        self._streaming.add(key)
        return key

    def finish_streaming(self, item_id: Any = None) -> None:
        if item_id is None:
            self._streaming.clear()
            return
        self._streaming.discard(fmt.text(item_id))

    def set_pending_approval(self, approval: Any) -> dict[str, Any] | None:
        if isinstance(approval, dict) and approval.get("callId"):
            self.pending_approval = {
                "callId": approval.get("callId"),
                "toolName": approval.get("toolName"),
                "arguments": approval.get("arguments") or {},
                "effect": approval.get("effect"),
                "reason": approval.get("reason"),
            }
        else:
            self.pending_approval = None
        return self.pending_approval

    def set_status(self, status: str) -> None:
        if self.thread:
            self.thread["status"] = fmt.text(status) or "idle"


__all__ = ["OPTIMISTIC_USER_KEY", "ThreadState", "TranscriptEntry"]

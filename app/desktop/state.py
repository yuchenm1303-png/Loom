"""Client-side thread state for the desktop transcript.

The App Server exposes a typed turn timeline through ``turns[].items[]``. This
module turns that durable timeline -- plus live ``item/*`` notifications -- into
a stable list of transcript entries the view can reconcile by key.

Two ordering details are important for an agent UI:
- older / partially migrated snapshots can have activity items in ``turns`` but
  keep conversation text only in top-level ``messages``; when event history is
  available we recover those message items at their real timestamps instead of
  prepending all prose before every command;
- a snapshot refresh must not append an uncommitted live process/tool to the end
  of the transcript, because that makes activity visibly jump below a newer
  assistant message.

Live text/output fragments are buffered between UI frames. Appending a token is
therefore O(1): it never recopies the whole accumulated answer just because the
provider chose a very small chunk size. Read/reconcile boundaries materialise the
fragments once, which keeps transport throughput independent from paint cadence.

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
_MESSAGE_ITEM_TYPES = {"user_message", "assistant_message"}
_STREAM_FIELDS = ("text", "stdout", "stderr")


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
        # (item key, field) -> provider fragments not yet materialised into the
        # canonical item string. Lists make token arrival O(1); a UI frame joins
        # each dirty field at most once.
        self._stream_fragments: dict[tuple[str, str], list[str]] = {}
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
        # This is the transcript paint boundary. Coalesce every provider chunk
        # received since the previous frame before signatures are calculated.
        self._flush_stream_fragments()
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
        # Runtime tabs are another read boundary and must observe complete output
        # through the moment they render.
        self._flush_stream_fragments()
        return [
            item
            for key in self._order
            if (item := self._items.get(key)) is not None
            and fmt.text(item.get("type")) == item_type
        ]

    def tool_items(self, predicate) -> list[dict[str, Any]]:
        return [item for item in self.items_of_type("tool_call") if predicate(item)]

    # ---- stream buffering ------------------------------------------------

    def _queue_stream_fragment(self, key: str, field_name: str, chunk: str) -> None:
        if not key or not chunk:
            return
        self._stream_fragments.setdefault((key, field_name), []).append(chunk)

    def _flush_item_fragments(self, key: str) -> None:
        """Materialise pending provider fragments for one canonical item."""
        item = self._items.get(key)
        if item is None:
            # An item can disappear during snapshot/thread replacement. Never
            # let orphaned fragments leak into a later item that reuses its key.
            for field_name in _STREAM_FIELDS:
                self._stream_fragments.pop((key, field_name), None)
            return
        for field_name in _STREAM_FIELDS:
            fragments = self._stream_fragments.pop((key, field_name), None)
            if fragments:
                item[field_name] = fmt.text(item.get(field_name)) + "".join(fragments)

    def _flush_stream_fragments(self, keys: Iterable[str] | None = None) -> None:
        if not self._stream_fragments:
            return
        if keys is None:
            targets = {key for key, _field_name in self._stream_fragments}
        else:
            targets = {fmt.text(key) for key in keys if fmt.text(key)}
        for key in targets:
            self._flush_item_fragments(key)

    # ---- timeline helpers ------------------------------------------------

    @staticmethod
    def _item_stamp(item: dict[str, Any]) -> str:
        """Return a sortable server timestamp when one is available."""
        return fmt.text(item.get("createdAt") or item.get("created_at")).strip()

    @classmethod
    def _insert_by_time(
        cls,
        timeline: list[dict[str, Any]],
        item: dict[str, Any],
    ) -> None:
        """Insert one recovered/live item without disturbing equal-time order."""
        stamp = cls._item_stamp(item)
        if not stamp:
            timeline.append(item)
            return

        for index, existing in enumerate(timeline):
            existing_stamp = cls._item_stamp(existing)
            if existing_stamp and existing_stamp > stamp:
                timeline.insert(index, item)
                return
        timeline.append(item)

    @classmethod
    def _merge_preserved_items(
        cls,
        durable: list[dict[str, Any]],
        preserved: list[dict[str, Any]],
        old_order: list[str],
    ) -> list[dict[str, Any]]:
        """Reinsert live-only rows at their original chronological position.

        Most live items carry ``createdAt`` from ``item/started``. That is the
        strongest ordering signal. If a legacy notification lacks a timestamp,
        fall back to its old neighbours rather than blindly appending it after
        the newest durable assistant message.
        """
        merged = list(durable)
        known = {fmt.text(item.get("id")) for item in merged}
        old_index = {key: index for index, key in enumerate(old_order)}

        for item in preserved:
            key = fmt.text(item.get("id"))
            if not key or key in known:
                continue

            if cls._item_stamp(item):
                cls._insert_by_time(merged, item)
                known.add(key)
                continue

            position = old_index.get(key)
            inserted = False
            if position is not None:
                # Prefer the next surviving neighbour: placing before it keeps a
                # live command between the same two messages after refresh.
                for neighbour in old_order[position + 1 :]:
                    if neighbour not in known:
                        continue
                    for target, existing in enumerate(merged):
                        if fmt.text(existing.get("id")) == neighbour:
                            merged.insert(target, item)
                            inserted = True
                            break
                    if inserted:
                        break

                if not inserted:
                    for neighbour in reversed(old_order[:position]):
                        if neighbour not in known:
                            continue
                        for target, existing in enumerate(merged):
                            if fmt.text(existing.get("id")) == neighbour:
                                merged.insert(target + 1, item)
                                inserted = True
                                break
                        if inserted:
                            break

            if not inserted:
                merged.append(item)
            known.add(key)

        return merged

    # ---- writes ----------------------------------------------------------

    def reset(self) -> None:
        self.thread_id = ""
        self.thread = {}
        self.snapshot = {}
        self.pending_approval = None
        self._items.clear()
        self._order.clear()
        self._streaming.clear()
        self._stream_fragments.clear()
        self._optimistic_user = ""

    def apply_snapshot(self, snapshot: dict[str, Any]) -> bool:
        """Replace durable state. Returns False when the payload has no thread."""
        thread = snapshot.get("thread")
        if not isinstance(thread, dict):
            return False
        thread_id = fmt.text(thread.get("id")).strip()
        if not thread_id:
            return False

        # A snapshot can race the UI frame that would normally materialise live
        # fragments. Flush before copying the old state so preserved live-only
        # rows carry every byte received from the provider.
        self._flush_stream_fragments()
        switching = thread_id != self.thread_id
        old_order = list(self._order)
        old_items = dict(self._items)

        self.thread_id = thread_id
        self.thread = dict(thread)
        self.snapshot = snapshot
        approval = snapshot.get("pendingApproval")
        self.pending_approval = approval if isinstance(approval, dict) and approval.get("callId") else None

        if switching:
            self._streaming.clear()
            self._stream_fragments.clear()
            self._optimistic_user = ""
            old_order = []
            old_items = {}

        durable = list(self._snapshot_items(snapshot))
        durable_keys = {fmt.text(item.get("id")) for item in durable}

        # Keep live-only items that the snapshot has not committed yet. They
        # must stay where they originally occurred; appending them at the end is
        # what made command/file rows jump below a newer assistant message.
        preserved_keys = [
            key
            for key in old_order
            if key not in durable_keys and key in self._streaming
        ]
        preserved_items = [
            old_items[key]
            for key in preserved_keys
            if key in old_items
        ]
        if preserved_items:
            durable = self._merge_preserved_items(durable, preserved_items, old_order)

        self._items = {fmt.text(item.get("id")): item for item in durable}
        self._order = [fmt.text(item.get("id")) for item in durable]

        self._streaming &= set(self._order)
        # No fragments should survive replacement of the canonical item map.
        self._stream_fragments.clear()
        if any(fmt.text(item.get("type")) == "user_message" for item in durable):
            self._optimistic_user = ""
        return True

    @classmethod
    def _snapshot_items(cls, snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Return the best available chronological durable transcript.

        Modern snapshots already carry messages and activity together in
        ``turns[].items[]``. Older snapshots sometimes carry only activity there
        and leave prose in top-level ``messages``. When raw events are present,
        recover the user/assistant items from those events and merge them into
        their true timestamp positions. Only if event history cannot help do we
        fall back to the old plain-message list.
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
                if item_type in _MESSAGE_ITEM_TYPES:
                    has_messages = True
                timeline.append(item)

        if has_messages:
            yield from timeline
            return

        recovered = list(cls._event_message_items(snapshot))
        if recovered:
            merged = list(timeline)
            for item in recovered:
                cls._insert_by_time(merged, item)
            yield from merged
            return

        # Last-resort compatibility path for snapshots whose event history was
        # pruned. There is no trustworthy timing data left, so preserving the
        # conversation text is preferable to dropping it entirely.
        yield from cls._message_items(snapshot)
        yield from timeline

    @staticmethod
    def _event_message_items(snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Recover renderable message items from the ordered Runtime event log."""
        for index, event in enumerate(snapshot.get("events") or []):
            if not isinstance(event, dict):
                continue
            event_id = fmt.text(event.get("eventId") or event.get("event_id")).strip()
            if not event_id:
                continue
            kind = fmt.text(event.get("kind")).strip().casefold()
            data = event.get("data")
            if not isinstance(data, dict):
                data = {}

            if kind == "user_message":
                item_type = "user_message"
                text = fmt.text(data.get("text"))
                item_id = f"user:{event_id}"
            elif kind == "model_response":
                item_type = "assistant_message"
                text = fmt.text(data.get("text"))
                if not text.strip():
                    continue
                item_id = f"assistant:{event_id}"
            else:
                continue

            if not text.strip():
                continue
            yield {
                "id": item_id,
                "threadId": fmt.text(event.get("threadId")) or None,
                "turnId": fmt.text(event.get("turnId")) or None,
                "type": item_type,
                "status": "completed",
                "text": text,
                "createdAt": fmt.text(event.get("createdAt")),
                "updatedAt": fmt.text(event.get("createdAt")),
                "eventOrder": index,
            }

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

        # Completion/status frames can arrive before the sampled UI frame. Fold
        # buffered deltas into the existing item before merging server metadata.
        self._flush_item_fragments(key)
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
        """Queue streamed assistant text. Returns the affected key."""
        key = fmt.text(item_id)
        chunk = fmt.text(chunk)
        if not key or not chunk:
            return ""
        item = self._items.get(key)
        if item is None:
            item = {"id": key, "type": "assistant_message", "status": "running", "text": ""}
            self._items[key] = item
            self._order.append(key)
        self._queue_stream_fragment(key, "text", chunk)
        self._streaming.add(key)
        return key

    def append_process_output(self, item_id: Any, *, stdout: str = "", stderr: str = "") -> str:
        """Queue process output without recopying the whole terminal buffer."""
        key = fmt.text(item_id)
        if not key or not (stdout or stderr):
            return ""
        item = self._items.get(key)
        if item is None:
            item = {"id": key, "type": "process", "status": "running"}
            self._items[key] = item
            self._order.append(key)
        if stdout:
            self._queue_stream_fragment(key, "stdout", fmt.text(stdout))
        if stderr:
            self._queue_stream_fragment(key, "stderr", fmt.text(stderr))
        self._streaming.add(key)
        return key

    def finish_streaming(self, item_id: Any = None) -> None:
        # Terminal transitions are another materialisation boundary. Never let a
        # final frame hide bytes still sitting in fragment lists.
        if item_id is None:
            self._flush_stream_fragments()
            self._streaming.clear()
            return
        key = fmt.text(item_id)
        self._flush_item_fragments(key)
        self._streaming.discard(key)

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

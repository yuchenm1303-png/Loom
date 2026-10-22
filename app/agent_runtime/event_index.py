"""A header-only index of ``events.jsonl``, so that a window of turns is read without parsing the log around it.

Opening a conversation shows its newest turns. Finding them by parsing the log (every tool output, every request the
model was ever sent) cost 100-200 ms on a long session whatever the window was, and parsed it again for each of the
next conversations once more than a few were in use. A line is looked at here only as far as its header, once; the
window is then read from the file at the offsets the index holds, and only those lines are parsed.

The log is append-only, so the index is extended with what was appended since the last time. It is validated the way
``EventParseCache`` validates its incremental reads: same file, bigger, and the bytes at both ends of what was indexed
are still the same; anything else rebuilds it.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from array import array
from collections import OrderedDict
from itertools import chain

# `_append_event` writes {"event_id","session_id","turn_id","kind","created_at","data"} in this order, so the two fields a
# window needs are next to each other and well inside the first few hundred bytes of the line.
_HEADER = re.compile(rb'"turn_id":"([^"\\]*)","kind":"([^"\\]*)"')
_HEADER_SPAN = 512
_SENTINEL = 256
# Indexing reads the new part of a log this much at a time, so a very long one is not held in memory whole.
_CHUNK = 8 << 20


def _both_ends(handle, offset: int) -> tuple[bytes, bytes]:
    handle.seek(0)
    prefix = handle.read(min(_SENTINEL, offset))
    handle.seek(max(0, offset - _SENTINEL))
    boundary = handle.read(min(_SENTINEL, offset))
    return prefix, boundary


class LogIndex:
    """What is known about the complete lines of one log: where each is, whose it is, and what kind."""

    __slots__ = ("file_id", "offset", "prefix", "boundary", "starts", "ends", "turns", "kinds", "turn_lines",
                 "turn_order", "approvals", "completed", "_words")

    def __init__(self, file_id: tuple[int, int]) -> None:
        self.file_id = file_id
        self.offset = 0
        self.prefix = b""
        self.boundary = b""
        self.starts = array("Q")
        self.ends = array("Q")
        self.turns: list[str] = []
        self.kinds: list[str] = []
        # Line numbers of each turn's events, in order; the turns in order of their first event.
        self.turn_lines: dict[str, list[int]] = {}
        self.turn_order: list[str] = []
        # The lines of tool_approval_required, and the newest turn_completed line of each turn.
        self.approvals: list[int] = []
        self.completed: dict[str, int] = {}
        self._words: dict[bytes, str] = {}

    @property
    def lines(self) -> int:
        return len(self.starts)

    def _word(self, raw: bytes) -> str:
        word = self._words.get(raw)
        if word is None:
            word = sys.intern(raw.decode("utf-8", "replace"))
            if len(self._words) < 4096:
                self._words[raw] = word
        return word

    def extend(self, handle, size: int) -> None:
        """Index the complete lines between what is known and ``size``. A last line without its newline is not yet a line.

        An error leaves the index half extended: whoever holds it drops it (see ``LogIndexes.current``)."""
        read_size = _CHUNK
        while self.offset < size:
            handle.seek(self.offset)
            raw = handle.read(min(read_size, size - self.offset))
            complete = raw.rfind(b"\n") + 1
            if not complete:
                if self.offset + len(raw) >= size:
                    break  # what is left is a last line without its newline
                read_size *= 2  # a line longer than what was read: read more of it
                continue
            read_size = _CHUNK
            self._index(raw, complete)
        self.prefix, self.boundary = _both_ends(handle, self.offset)

    def _index(self, raw: bytes, complete: int) -> None:
        """Index the lines of ``raw[:complete]``, which starts at ``self.offset`` and ends with a newline."""
        base = self.offset
        position = 0
        search = _HEADER.search
        while position < complete:
            end = raw.find(b"\n", position, complete)
            if end < 0:
                break
            if end - position < 64 and not raw[position:end].strip():
                position = end + 1
                continue
            match = search(raw, position, min(end, position + _HEADER_SPAN))
            if match is not None:
                turn, kind = self._word(match.group(1)), self._word(match.group(2))
            else:
                # Not the usual shape (another writer, an escape in an id): the line says what it is when it is parsed.
                payload = json.loads(raw[position:end])
                turn = sys.intern(str(payload.get("turn_id") or ""))
                kind = sys.intern(str(payload.get("kind") or ""))
            number = len(self.starts)
            self.starts.append(base + position)
            self.ends.append(base + end)
            self.turns.append(turn)
            self.kinds.append(kind)
            key = turn.strip()
            if key:
                if key not in self.turn_lines:
                    self.turn_lines[key] = []
                    self.turn_order.append(key)
                if turn == key:
                    self.turn_lines[key].append(number)
            if kind == "tool_approval_required":
                self.approvals.append(number)
            elif kind == "turn_completed":
                self.completed[turn] = number
            position = end + 1
        self.offset = base + complete

    def holds(self, handle, file_id: tuple[int, int], size: int) -> str:
        """``"same"`` when nothing was added, ``"grown"`` when the log only grew, ``"changed"`` otherwise."""
        if file_id != self.file_id or size < self.offset:
            return "changed"
        if (self.prefix, self.boundary) != _both_ends(handle, self.offset):
            return "changed"
        return "same" if size == self.offset else "grown"

    def window(self, limit: int, before_turn_id: str = "") -> tuple[list[int], bool]:
        """The line numbers of the newest ``limit`` turns before ``before_turn_id`` (all of them when it is empty), in log
        order, and whether older turns exist. Turns are ordered by their first event, as the full-log reader orders them."""
        order = self.turn_order
        end = len(order)
        if before_turn_id:
            try:
                end = order.index(before_turn_id)
            except ValueError as exc:
                raise ValueError("thread/read beforeTurnId is not present in this thread") from exc
        start = max(0, end - limit)
        selected = chain.from_iterable(self.turn_lines[turn] for turn in order[start:end])
        return sorted(selected), start > 0

    def read(self, handle, numbers: list[int], gap: int = 1 << 20) -> list[bytes]:
        """The bytes of the given lines (ascending). Lines close together are read in one piece."""
        lines: list[bytes] = []
        index = 0
        while index < len(numbers):
            last = index
            while last + 1 < len(numbers) and self.starts[numbers[last + 1]] - self.ends[numbers[last]] <= gap:
                last += 1
            low, high = self.starts[numbers[index]], self.ends[numbers[last]]
            handle.seek(low)
            blob = handle.read(high - low)
            for number in numbers[index:last + 1]:
                lines.append(blob[self.starts[number] - low:self.ends[number] - low])
            index = last + 1
        return lines


class LogIndexes:
    """The indexes of the logs in use, least recently used out first. Callers hold the log's session lock."""

    def __init__(self, *, max_logs: int = 12, max_lines: int = 600_000) -> None:
        self.max_logs = max_logs
        self.max_lines = max_lines
        self._entries: OrderedDict[str, LogIndex] = OrderedDict()
        self._lock = threading.Lock()
        self.built = 0
        self.extended = 0

    def current(self, path, handle) -> LogIndex:
        """The index of ``path`` brought up to the end of the file ``handle`` has open."""
        stat = path.stat()
        file_id = (stat.st_dev, stat.st_ino)
        key = str(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
        if entry is not None:
            state = entry.holds(handle, file_id, stat.st_size)
            if state == "same":
                return entry
            if state == "grown":
                try:
                    entry.extend(handle, stat.st_size)
                except BaseException:
                    self.forget(path)  # a half-extended index must not be used again
                    raise
                self.extended += 1
                self._trim()
                return entry
        entry = LogIndex(file_id)
        entry.extend(handle, stat.st_size)
        self.built += 1
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
        self._trim()
        return entry

    def forget(self, path) -> None:
        with self._lock:
            self._entries.pop(str(path), None)

    def _trim(self) -> None:
        with self._lock:
            while len(self._entries) > self.max_logs or (
                len(self._entries) > 1 and sum(entry.lines for entry in self._entries.values()) > self.max_lines
            ):
                self._entries.popitem(last=False)

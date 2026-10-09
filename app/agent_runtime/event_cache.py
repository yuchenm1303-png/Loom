"""Bounded per-store JSONL parse cache; callers hold the durable journal lock."""
from collections import OrderedDict
from copy import deepcopy
import json
import sys
import threading

_ATOMS = frozenset((str, int, float, bool, type(None)))


def _clone(value):
    """Copy parsed JSON so a caller cannot reach the cached objects.

    ``copy.deepcopy`` records every object it visits so that shared and cyclic
    references survive the copy. ``json.loads`` produces neither, and that
    bookkeeping made copying a long transcript several times slower than
    parsing it.
    """
    kind = type(value)
    if kind is dict:
        return {key: _clone(item) for key, item in value.items()}
    if kind is list:
        return [_clone(item) for item in value]
    if kind in _ATOMS:
        return value
    return deepcopy(value)


class _CountingReader:
    def __init__(self, handle):
        self.handle = handle
        self.bytes_read = 0

    def seek(self, *args):
        return self.handle.seek(*args)

    def tell(self):
        return self.handle.tell()

    def read(self, *args):
        raw = self.handle.read(*args)
        self.bytes_read += len(raw)
        return raw


class EventParseCache:
    def __init__(self, *, max_bytes=32_000_000, max_entries=16):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._entries = OrderedDict()
        # Where a complete read last proved too large to keep. An append-only log
        # only grows, so until it is replaced or truncated there is no need to
        # weigh the same oversized parse again on every call.
        self._oversized = {}
        self._lock = threading.RLock()
        self._parsed = self._read = self._hits = 0

    def metrics(self):
        with self._lock:
            return {"parsed_records": self._parsed, "bytes_read": self._read,
                    "cache_hits": self._hits, "entries": len(self._entries),
                    "cached_bytes": sum(e["weight"] for e in self._entries.values())}

    @staticmethod
    def _read_bytes(handle, start, size=-1):
        handle.seek(start)
        return handle.read(size)

    @staticmethod
    def _weight(value, seen=None, limit=None):
        """Account for retained Python objects, including nested JSON values.

        With ``limit`` the walk stops as soon as the total passes it. Whoever
        asks only needs to know an entry is too large to keep, and measuring a
        70 MB parse tree to the last object cost more than parsing it.
        """
        seen = set() if seen is None else seen
        total = 0
        pending = [value]
        while pending:
            item = pending.pop()
            identity = id(item)
            if identity in seen:
                continue
            seen.add(identity)
            total += sys.getsizeof(item)
            if limit is not None and total > limit:
                return total
            if isinstance(item, dict):
                for key, child in item.items():
                    pending.append(key)
                    pending.append(child)
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return total

    @staticmethod
    def _deliver(records, build, shared):
        """Hand records to a caller; ``shared`` ones belong to the cache and are copied."""
        if build is None:
            return _clone(records) if shared else records
        return [build(record, shared) for record in records]

    def read(self, path, limit, tail_reader, *, project=None, parse=None, build=None):
        """Parsed records of ``path``, one per line, shared by every reader of this view.

        ``project`` reshapes a parsed record. ``parse`` replaces ``json.loads`` for
        complete lines, so a view can leave out the body of records it never reads
        or return ``None`` to drop a line altogether. An unterminated final line is
        always validated as a whole before it is shown.

        Records are returned as copies. ``build(record, shared)`` makes the caller's
        own object from one instead, so only what the caller keeps is copied.
        """
        key = (str(path), limit, project, parse)
        parse_line = json.loads if parse is None else parse
        if not path.is_file():
            with self._lock:
                self._entries.pop(key, None)
                self._oversized.pop(key, None)
            return []
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry["signature"] == signature:
                self._entries.move_to_end(key)
                self._hits += 1
                cached = entry["visible"]
            else:
                cached = None
        if cached is not None:
            return self._deliver(cached, build, shared=True)

        # Callers hold the journal lock for this session. Only shared LRU
        # bookkeeping needs the store-wide lock: IO, parsing and copying a long
        # transcript must not prevent another session from loading its preview.
        parsed = 0
        with path.open("rb") as file:
            handle = _CountingReader(file)
            try:
                incremental = (limit is None and entry is not None and signature[:2] == entry["signature"][:2]
                               and stat.st_size > entry["signature"][2])
                if incremental:
                    prefix = self._read_bytes(handle, 0, len(entry["prefix"]))
                    boundary = self._read_bytes(handle, max(0, entry["offset"] - 256), min(256, entry["offset"]))
                    incremental = prefix == entry["prefix"] and boundary == entry["boundary"]
                if incremental:
                    raw = self._read_bytes(handle, entry["offset"])
                    records = list(entry["records"])
                    offset = entry["offset"]
                else:
                    records = []
                    if limit is None:
                        raw = self._read_bytes(handle, 0)
                        offset = 0
                    else:
                        # A cold bounded read never parses or retains old history.
                        lines = tail_reader(handle, limit)
                        raw = b"".join(lines)
                        offset = stat.st_size - len(raw)
                complete_end = raw.rfind(b"\n") + 1
                complete = raw[:complete_end]
                trailing = raw[complete_end:]
                fresh = []
                for line in complete.splitlines():
                    if line.strip():
                        payload = parse_line(line)
                        parsed += 1
                        if payload is not None:
                            fresh.append(project(payload) if project is not None else payload)
                records.extend(fresh)
                if limit is not None:
                    records = records[-limit:]
                visible = list(records)
                if trailing.strip():
                    try:
                        payload = json.loads(trailing)
                        if parse is not None:
                            payload = parse(trailing)
                        parsed += 1
                        if payload is not None:
                            visible.append(project(payload) if project is not None else payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                if limit is not None:
                    visible = visible[-limit:]
                offset = stat.st_size - len(trailing)
                # Bounded views reload the bounded tail on changes. They do not
                # need append sentinels, nor additional reads outside that tail.
                prefix = self._read_bytes(handle, 0, min(256, offset)) if limit is None else b""
                boundary = self._read_bytes(handle, max(0, offset - 256), min(256, offset)) if limit is None else b""
                updated = {"signature": signature, "records": records, "visible": visible,
                           "offset": offset, "prefix": prefix, "boundary": boundary}
                too_large = self._oversized.get(key) if limit is None else None
                if too_large is not None and too_large[:2] == signature[:2] and signature[2] >= too_large[2]:
                    record_weight, weight = 0, self.max_bytes + 1
                else:
                    # A log grows by appending, so only what was just parsed needs
                    # weighing. Walking every kept record again for each new event
                    # made reading a long session's projection cost more with each step.
                    record_weight = entry["record_weight"] if incremental else 0
                    for record in (fresh if limit is None else records):
                        record_weight += self._weight(record)
                        if record_weight > self.max_bytes:
                            break
                    held = {id(record) for record in records}
                    weight = (record_weight + sys.getsizeof(updated) + sys.getsizeof(records) + sys.getsizeof(visible)
                              + sys.getsizeof(prefix) + sys.getsizeof(boundary) + sys.getsizeof(trailing)
                              + sum(self._weight(record) for record in visible if id(record) not in held))
                updated["record_weight"] = record_weight
                updated["weight"] = weight
            finally:
                with self._lock:
                    self._read += handle.bytes_read
                    self._parsed += parsed
        retained = weight <= self.max_bytes
        with self._lock:
            self._entries.pop(key, None)
            if retained:
                self._entries[key] = updated
                self._oversized.pop(key, None)
            elif limit is None:
                self._oversized.pop(key, None)
                self._oversized[key] = signature[:3]
                while len(self._oversized) > 1024:
                    self._oversized.pop(next(iter(self._oversized)))
            while (len(self._entries) > self.max_entries
                   or sum(e["weight"] for e in self._entries.values()) > self.max_bytes):
                self._entries.popitem(last=False)
        # What the cache did not keep has no other holder, so the caller can
        # have the parsed objects themselves; copying them would only repeat
        # the cost of parsing a history too large to cache.
        return self._deliver(visible, build, shared=retained)

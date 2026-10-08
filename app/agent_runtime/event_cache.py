"""Bounded per-store JSONL parse cache; callers hold the durable journal lock."""
from collections import OrderedDict
from copy import deepcopy
import json
import sys
import threading


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
    def __init__(self, *, max_bytes=8_000_000, max_entries=16):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._entries = OrderedDict()
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
    def _weight(value, seen=None):
        """Account for retained Python objects, including nested JSON values."""
        seen = set() if seen is None else seen
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sys.getsizeof(value)
        if isinstance(value, dict):
            size += sum(EventParseCache._weight(k, seen) + EventParseCache._weight(v, seen)
                        for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            size += sum(EventParseCache._weight(item, seen) for item in value)
        return size

    def read(self, path, limit, tail_reader, *, project=None):
        key = (str(path), limit, project)
        if not path.is_file():
            with self._lock:
                self._entries.pop(key, None)
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
            return deepcopy(cached)

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
                for line in complete.splitlines():
                    if line.strip():
                        payload = json.loads(line)
                        records.append(project(payload) if project is not None else payload)
                        parsed += 1
                if limit is not None:
                    records = records[-limit:]
                visible = list(records)
                if trailing.strip():
                    try:
                        payload = json.loads(trailing)
                        visible.append(project(payload) if project is not None else payload)
                        parsed += 1
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
                weight = self._weight(updated) + sys.getsizeof(trailing)
                updated["weight"] = weight
            finally:
                with self._lock:
                    self._read += handle.bytes_read
                    self._parsed += parsed
        with self._lock:
            self._entries.pop(key, None)
            if weight <= self.max_bytes:
                self._entries[key] = updated
            while (len(self._entries) > self.max_entries
                   or sum(e["weight"] for e in self._entries.values()) > self.max_bytes):
                self._entries.popitem(last=False)
        return deepcopy(visible)

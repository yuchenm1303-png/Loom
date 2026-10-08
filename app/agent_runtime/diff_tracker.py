from __future__ import annotations

import difflib
import threading
from collections import OrderedDict
from dataclasses import dataclass


_MAX_TRACKED_TURNS = 64
_MAX_DIFF_CHARS = 40_000


@dataclass(frozen=True, slots=True)
class DiffSnapshot:
    revision: int
    paths: tuple[str, ...]
    diff: str
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "paths": list(self.paths),
            "diff": self.diff,
            "truncated": self.truncated,
        }


@dataclass(slots=True)
class _TrackedFile:
    baseline: str | None
    current: str | None


class TurnDiffTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, _TrackedFile] = {}
        self._revision = 0
        self._snapshot_cache: tuple[int, int, DiffSnapshot] | None = None

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def record_text_change(
        self,
        path: str,
        *,
        before: str | None,
        after: str | None,
    ) -> None:
        relative = str(path or "").replace("\\", "/").lstrip("/")
        if not relative:
            raise ValueError("diff path must not be empty")
        if before == after:
            return
        with self._lock:
            tracked = self._files.get(relative)
            if tracked is None:
                tracked = _TrackedFile(baseline=before, current=after)
                self._files[relative] = tracked
            else:
                tracked.current = after
            if tracked.baseline == tracked.current:
                self._files.pop(relative, None)
            self._revision += 1
            self._snapshot_cache = None

    def snapshot(self, *, max_chars: int = _MAX_DIFF_CHARS) -> DiffSnapshot:
        limit = max(1, int(max_chars))
        with self._lock:
            revision = self._revision
            cached = self._snapshot_cache
            if cached is not None and cached[:2] == (revision, limit):
                return cached[2]
            files = {
                path: _TrackedFile(item.baseline, item.current)
                for path, item in self._files.items()
            }
        chunks: list[str] = []
        for path in sorted(files):
            item = files[path]
            before_lines = [] if item.baseline is None else item.baseline.splitlines(keepends=True)
            after_lines = [] if item.current is None else item.current.splitlines(keepends=True)
            from_name = "/dev/null" if item.baseline is None else f"a/{path}"
            to_name = "/dev/null" if item.current is None else f"b/{path}"
            chunks.extend(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=from_name,
                    tofile=to_name,
                    lineterm="\n",
                )
            )
        diff = "".join(chunks)
        truncated = len(diff) > limit
        if truncated:
            diff = diff[:limit] + "\n... diff truncated ...\n"
        result = DiffSnapshot(
            revision=revision,
            paths=tuple(sorted(files)),
            diff=diff,
            truncated=truncated,
        )
        with self._lock:
            if self._revision == revision:
                self._snapshot_cache = (revision, limit, result)
        return result


class DiffTrackerRegistry:
    def __init__(self, *, max_turns: int = _MAX_TRACKED_TURNS) -> None:
        self.max_turns = max(1, int(max_turns))
        self._lock = threading.RLock()
        self._trackers: OrderedDict[tuple[str, str], TurnDiffTracker] = OrderedDict()

    def for_turn(self, session_id: str, turn_id: str) -> TurnDiffTracker:
        key = (str(session_id), str(turn_id))
        with self._lock:
            tracker = self._trackers.get(key)
            if tracker is None:
                tracker = TurnDiffTracker()
                self._trackers[key] = tracker
            else:
                self._trackers.move_to_end(key)
            while len(self._trackers) > self.max_turns:
                self._trackers.popitem(last=False)
            return tracker

    def snapshot(self, session_id: str, turn_id: str) -> DiffSnapshot:
        return self.for_turn(session_id, turn_id).snapshot()


__all__ = ["DiffSnapshot", "DiffTrackerRegistry", "TurnDiffTracker"]

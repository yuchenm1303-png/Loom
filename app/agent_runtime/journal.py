"""Redo journal for atomic event + session commits, with cross-process exclusion."""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager


_guard = threading.Lock()
_locks = {}


class ExecutionLease:
    """Reentrant thread lock plus a process lease for a complete session action."""

    def __init__(self, directory):
        self.directory = directory
        with _guard:
            self._lock = _locks.setdefault(str(directory / ".turn.lock"), threading.RLock())
        self._local = threading.local()

    def __enter__(self):
        self._lock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            return self
        handle = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle = (self.directory / ".turn.lock").open("a+b")
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._local.handle = handle
            self._local.depth = 1
            return self
        except OSError as exc:
            if handle is not None:
                handle.close()
            self._lock.release()
            raise RuntimeError("session execution lease unavailable; another process may own this session") from exc

    def __exit__(self, *args):
        self._local.depth -= 1
        try:
            if not self._local.depth:
                # Closing the descriptor releases its process lock on both OSes.
                self._local.handle.close()
                self._local.handle = None
        finally:
            self._lock.release()


@contextmanager
def session_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with _guard:
        lock = _locks.setdefault(str(directory), threading.RLock())
    with lock, (directory / ".commit.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _chmod_private(path) -> None:
    """Best-effort user-only permissions for local runtime state."""

    try:
        path.chmod(0o600)
    except OSError:
        pass


def atomic_json(path, value):
    """Atomically persist private runtime JSON without a permissive temp window."""

    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = None
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _chmod_private(path)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        temp.unlink(missing_ok=True)


def repair_tail(path):
    if not path.exists():
        return
    with path.open("r+b") as handle:
        data = handle.read()
        if not data or data.endswith(b"\n"):
            return
        boundary = data.rfind(b"\n") + 1
        try:
            json.loads(data[boundary:])
        except (ValueError, UnicodeDecodeError):
            handle.truncate(boundary)
        else:
            handle.seek(0, 2)
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def recover(directory):
    journal = directory / ".pending-commit.json"
    if not journal.exists():
        return
    payload = json.loads(journal.read_text(encoding="utf-8"))
    path = directory / "events.jsonl"
    repair_tail(path)
    event = payload["event"]
    found = False
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            found = any(json.loads(line)["event_id"] == event["event_id"] for line in handle if line.strip())
    if not found:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    atomic_json(directory / "session.json", payload["session"])
    journal.unlink()

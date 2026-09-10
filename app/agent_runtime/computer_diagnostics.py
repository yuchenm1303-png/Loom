from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class ComputerDiagnostics:
    """Structured diagnostics and replay traces for Computer Use.

    ``detailed`` records safe structured metadata, per-turn traces and screenshot
    manifests. ``raw`` additionally persists screenshots, full observations,
    instructions and provider responses. Credentials and transient typed text must
    never be passed to this boundary.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        mode = str(env.get("LOOM_COMPUTER_DIAGNOSTICS") or "detailed").strip().casefold()
        self.mode = mode if mode in {"off", "detailed", "raw"} else "detailed"
        root = str(env.get("LOOM_COMPUTER_LOG_DIR") or "").strip()
        self.root = Path(root).expanduser().resolve() if root else (Path.cwd() / ".loom" / "logs" / "computer-use").resolve()
        self.run_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._sequence = 0
        self.max_bytes = max(64 * 1024, int(env.get("LOOM_COMPUTER_LOG_MAX_BYTES") or 10 * 1024 * 1024))
        self.backups = max(1, min(20, int(env.get("LOOM_COMPUTER_LOG_BACKUPS") or 5)))
        self._operation: ContextVar[str] = ContextVar("loom_computer_operation", default="")
        if self.mode != "off":
            self.root.mkdir(parents=True, exist_ok=True)
            self.emit("diagnostics.started", mode=self.mode, pid=os.getpid(), log_dir=str(self.root))

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def raw(self) -> bool:
        return self.mode == "raw"

    def status(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "log_dir": str(self.root),
            "events_path": str(self.root / "events.jsonl"),
            "trace_root": str(self.root / "traces"),
            "snapshot_manifest": str(self.root / "computer-snapshots" / "manifest.jsonl"),
            "raw_screenshot_dir": str(self.root / "computer-snapshots" / "images"),
            "raw_screenshots_enabled": self.raw,
            "rotation": {"max_bytes": self.max_bytes, "backups": self.backups},
        }

    def operation_id(self) -> str:
        return uuid.uuid4().hex[:16]

    @contextmanager
    def bind(self, operation_id: str):
        token = self._operation.set(str(operation_id))
        try:
            yield
        finally:
            self._operation.reset(token)

    def emit(self, event: str, *, operation_id: str = "", **data: Any) -> None:
        if not self.enabled:
            return
        operation_id = str(operation_id or self._operation.get())
        record = self._record(event, operation_id=operation_id, **data)
        self._append_jsonl(self.root / "events.jsonl", record)

    def trace_path(self, session_id: str, turn_id: str) -> Path:
        session = _safe_path_id(session_id, fallback="no-session")
        turn = _safe_path_id(turn_id, fallback="no-turn")
        return self.root / "traces" / session / turn / "computer-trace.jsonl"

    def trace(self, session_id: str, turn_id: str, event: str, *, operation_id: str = "", **data: Any) -> str:
        """Append a per-turn replayable Computer Use trace record.

        The global event stream is useful for coarse debugging; this file is the
        ordered, turn-scoped trace used to replay a GUI task without digging
        through unrelated tool calls.
        """

        if not self.enabled:
            return ""
        operation_id = str(operation_id or self._operation.get())
        record = self._record(
            event,
            operation_id=operation_id,
            session_id=str(session_id or ""),
            turn_id=str(turn_id or ""),
            **data,
        )
        target = self.trace_path(session_id, turn_id)
        self._append_jsonl(target, record)
        return str(target)

    def save_screenshot(self, observation: Any, *, operation_id: str, phase: str) -> str:
        screenshot_path = ""
        if self.raw:
            name = f"{self._sequence + 1:06d}-{operation_id}-{phase}-{observation.observation_id}.png"
            target = self.root / "computer-snapshots" / "images" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(observation.image_png)
            screenshot_path = str(target)
        self.record_snapshot_manifest(
            observation,
            operation_id=operation_id,
            phase=phase,
            screenshot_path=screenshot_path,
        )
        return screenshot_path

    def record_snapshot_manifest(
        self,
        observation: Any,
        *,
        operation_id: str,
        phase: str,
        screenshot_path: str = "",
        session_id: str = "",
        turn_id: str = "",
    ) -> str:
        if not self.enabled:
            return ""
        frame = getattr(observation, "frame", None)
        active_window = getattr(observation, "active_window", None)
        record = self._record(
            "snapshot.manifest",
            operation_id=operation_id,
            session_id=str(session_id or ""),
            turn_id=str(turn_id or ""),
            phase=str(phase or ""),
            observation_id=str(getattr(observation, "observation_id", "") or ""),
            image_sha256=str(getattr(observation, "image_sha256", "") or ""),
            image_bytes=len(bytes(getattr(observation, "image_png", b"") or b"")),
            screenshot_path=str(screenshot_path or ""),
            frame=_call_dict(frame),
            active_window=_call_dict(active_window),
            controls_total=len(tuple(getattr(observation, "controls", ()) or ())),
            windows_total=len(tuple(getattr(observation, "windows", ()) or ())),
        )
        target = self.root / "computer-snapshots" / "manifest.jsonl"
        self._append_jsonl(target, record)
        return str(target)

    def _record(self, event: str, *, operation_id: str = "", **data: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_ns": time.monotonic_ns(),
                "run_id": self.run_id,
                "sequence": self._sequence,
                "event": str(event),
                "operation_id": str(operation_id),
                **_json_safe(data),
            }

    def _append_jsonl(self, target: Path, record: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(target)
            with target.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass

    def _rotate_if_needed(self, target: Path) -> None:
        if not target.exists() or target.stat().st_size < self.max_bytes:
            return
        oldest = target.with_name(f"{target.name}.{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = target.with_name(f"{target.name}.{index}")
            if source.exists():
                source.replace(target.with_name(f"{target.name}.{index + 1}"))
        target.replace(target.with_name(f"{target.name}.1"))


def _call_dict(value: Any) -> Any:
    if value is None:
        return None
    method = getattr(value, "to_dict", None)
    if callable(method):
        try:
            return _json_safe(method())
        except Exception:
            return repr(value)
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def _safe_path_id(value: str, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text)
    return safe[:96] or fallback


__all__ = ["ComputerDiagnostics"]

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
    """Process-local structured diagnostics for Computer Use.

    ``detailed`` records structured metadata. ``raw`` additionally persists
    screenshots, full observations, instructions and provider responses.
    Credentials and typed action text must never be passed to this boundary.
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
        with self._lock:
            self._sequence += 1
            operation_id = str(operation_id or self._operation.get())
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_ns": time.monotonic_ns(),
                "run_id": self.run_id,
                "sequence": self._sequence,
                "event": str(event),
                "operation_id": str(operation_id),
                **_json_safe(data),
            }
            target = self.root / "events.jsonl"
            self._rotate_if_needed(target)
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

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

    def save_screenshot(self, observation: Any, *, operation_id: str, phase: str) -> str:
        if not self.raw:
            return ""
        name = f"{self._sequence + 1:06d}-{operation_id}-{phase}-{observation.observation_id}.png"
        target = self.root / "screenshots" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(observation.image_png)
        return str(target)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


__all__ = ["ComputerDiagnostics"]

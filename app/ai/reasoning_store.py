from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .reasoning import ReasoningRequest


def _default_home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


class ReasoningConfigStore:
    """Small non-secret store for per-model reasoning preferences."""

    def __init__(self, home: str | Path | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home is not None else _default_home()
        self.path = self.home / "reasoning.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "selections": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "selections": {}}
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != 1:
            return {"version": 1, "selections": {}}
        selections = payload.get("selections")
        if not isinstance(selections, dict):
            payload["selections"] = {}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        target = self.path
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            temporary.write_text(text, encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, selection: str) -> ReasoningRequest | None:
        key = str(selection or "").strip()
        if not key:
            return None
        entry = (self._read().get("selections") or {}).get(key)
        if not isinstance(entry, dict):
            return None
        try:
            return ReasoningRequest.from_values(entry.get("kind"), entry.get("value"))
        except (TypeError, ValueError):
            return None

    def set(self, selection: str, reasoning: ReasoningRequest | None) -> None:
        key = str(selection or "").strip()
        if not key:
            raise ValueError("model selection must not be empty")
        payload = self._read()
        selections = dict(payload.get("selections") or {})
        if reasoning is None:
            selections.pop(key, None)
        else:
            selections[key] = reasoning.as_safe_dict()
        payload["version"] = 1
        payload["selections"] = selections
        self._write(payload)


__all__ = ["ReasoningConfigStore"]

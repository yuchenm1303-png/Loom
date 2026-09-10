from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "schemaVersion": 1,
    "capabilities": {
        "computerUse": True,
        "browserUse": True,
        "webSearch": True,
        "mcp": True,
        "skills": True,
        "toolSearch": True,
        "codeMode": True,
        "attachments": True,
        "stickers": True,
    },
}


class LoomSettingsStore:
    """Small durable settings store shared by every Loom desktop session.

    Settings live in the Loom runtime home rather than inside a conversation, so
    capability choices survive app restarts and apply consistently to new turns.
    Unknown keys are preserved for forward compatibility while known values are
    normalized against the current defaults.
    """

    def __init__(self, runtime_home: str | Path) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_home / "settings.json"

    def snapshot(self) -> dict[str, Any]:
        data = self._read_raw()
        return self._normalize(data)

    def set_capability(self, name: str, enabled: bool) -> dict[str, Any]:
        key = str(name or "").strip()
        if key not in DEFAULT_SETTINGS["capabilities"]:
            raise ValueError(f"unsupported capability setting: {key}")
        data = self.snapshot()
        capabilities = dict(data.get("capabilities") or {})
        capabilities[key] = bool(enabled)
        data["capabilities"] = capabilities
        self._write(data)
        return self.snapshot()

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
        data = deepcopy(raw)
        data["schemaVersion"] = int(DEFAULT_SETTINGS["schemaVersion"])
        raw_capabilities = raw.get("capabilities")
        capabilities = dict(raw_capabilities) if isinstance(raw_capabilities, dict) else {}
        for key, default in DEFAULT_SETTINGS["capabilities"].items():
            capabilities[key] = bool(capabilities.get(key, default))
        data["capabilities"] = capabilities
        return data

    def _write(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".json.tmp", dir=self.runtime_home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["DEFAULT_SETTINGS", "LoomSettingsStore"]

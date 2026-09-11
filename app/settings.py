from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


SETTINGS_UPDATE_PREFIX = "__setting__:"

DEFAULT_SETTINGS: dict[str, Any] = {
    "schemaVersion": 2,
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
    "appearance": {
        "scale": "100",
        "density": "comfortable",
        "reducedMotion": False,
        "conversationWidth": "balanced",
        "sidebarWidth": "standard",
        "inspectorWidth": "standard",
        "chatFontSize": 13,
        "messageLineHeight": "comfortable",
        "ambientEffects": True,
        "codeFont": "system",
        "codeFontSize": 12,
        "codeLineHeight": "comfortable",
        "codeWrap": False,
    },
    "shortcuts": {
        "newConversation": "Ctrl+N",
        "searchConversations": "Ctrl+K",
        "openSettings": "Ctrl+,",
        "focusComposer": "Ctrl+L",
        "toggleSidebar": "Ctrl+B",
        "toggleInspector": "Ctrl+Shift+I",
        "attachFiles": "Ctrl+Shift+A",
        "stopTask": "Escape",
    },
    "terminal": {
        "shell": "powershell",
        "encoding": "utf-8",
        "commandTimeoutSeconds": 120,
        "preserveBackgroundProcesses": True,
    },
    "browser": {
        "preferredEngine": "edge",
        "persistSessions": True,
    },
    "computer": {
        "verifyActions": True,
        "screenshotQuality": "balanced",
    },
    "privacy": {
        "telemetry": False,
        "crashReports": False,
    },
}

_ALLOWED_SETTING_PATHS: dict[str, tuple[type, Any]] = {
    "appearance.scale": (str, {"90", "100", "110", "120", "130"}),
    "appearance.density": (str, {"compact", "comfortable", "spacious"}),
    "appearance.reducedMotion": (bool, None),
    "appearance.conversationWidth": (str, {"focused", "balanced", "wide"}),
    "appearance.sidebarWidth": (str, {"compact", "standard", "wide"}),
    "appearance.inspectorWidth": (str, {"compact", "standard", "wide"}),
    "appearance.chatFontSize": (int, range(11, 19)),
    "appearance.messageLineHeight": (str, {"compact", "comfortable", "relaxed"}),
    "appearance.ambientEffects": (bool, None),
    "appearance.codeFont": (str, None),
    "appearance.codeFontSize": (int, range(10, 19)),
    "appearance.codeLineHeight": (str, {"compact", "comfortable", "relaxed"}),
    "appearance.codeWrap": (bool, None),
    "shortcuts.newConversation": (str, None),
    "shortcuts.searchConversations": (str, None),
    "shortcuts.openSettings": (str, None),
    "shortcuts.focusComposer": (str, None),
    "shortcuts.toggleSidebar": (str, None),
    "shortcuts.toggleInspector": (str, None),
    "shortcuts.attachFiles": (str, None),
    "shortcuts.stopTask": (str, None),
    "terminal.shell": (str, {"powershell", "cmd", "git-bash", "wsl"}),
    "terminal.encoding": (str, {"utf-8", "system"}),
    "terminal.commandTimeoutSeconds": (int, range(15, 1801)),
    "terminal.preserveBackgroundProcesses": (bool, None),
    "browser.preferredEngine": (str, {"edge", "chrome", "system"}),
    "browser.persistSessions": (bool, None),
    "computer.verifyActions": (bool, None),
    "computer.screenshotQuality": (str, {"fast", "balanced", "high"}),
    "privacy.telemetry": (bool, None),
    "privacy.crashReports": (bool, None),
}


class LoomSettingsStore:
    """Durable application settings shared by Loom desktop sessions.

    ``settings/set`` historically accepted only capability booleans. The
    ``__setting__:`` envelope lets newer desktop builds persist typed settings
    through that stable RPC until a protocol-v2 generic settings method lands.
    Older clients remain fully compatible because normal capability names still
    take the original path.
    """

    def __init__(self, runtime_home: str | Path) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_home / "settings.json"

    def snapshot(self) -> dict[str, Any]:
        return self._normalize(self._read_raw())

    def set_capability(self, name: str, enabled: bool) -> dict[str, Any]:
        key = str(name or "").strip()
        if key.startswith(SETTINGS_UPDATE_PREFIX):
            return self._set_enveloped_setting(key[len(SETTINGS_UPDATE_PREFIX):])
        if key not in DEFAULT_SETTINGS["capabilities"]:
            raise ValueError(f"unsupported capability setting: {key}")
        data = self.snapshot()
        capabilities = dict(data.get("capabilities") or {})
        capabilities[key] = bool(enabled)
        data["capabilities"] = capabilities
        self._write(data)
        return self.snapshot()

    def _set_enveloped_setting(self, payload_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid settings update envelope") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid settings update envelope")
        path = str(payload.get("path") or "").strip()
        if path not in _ALLOWED_SETTING_PATHS:
            raise ValueError(f"unsupported setting path: {path}")
        value = self._validate_setting(path, payload.get("value"))
        section, key = path.split(".", 1)
        data = self.snapshot()
        bucket = dict(data.get(section) or {})
        bucket[key] = value
        data[section] = bucket
        self._write(data)
        return self.snapshot()

    @staticmethod
    def _validate_setting(path: str, value: Any) -> Any:
        expected_type, allowed = _ALLOWED_SETTING_PATHS[path]
        if expected_type is int and isinstance(value, bool):
            raise ValueError(f"invalid value for setting {path}")
        if not isinstance(value, expected_type):
            raise ValueError(f"invalid value type for setting {path}")
        if expected_type is str:
            value = value.strip()
            if not value:
                raise ValueError(f"setting {path} cannot be empty")
            if path == "appearance.codeFont":
                return value[:120]
            if path.startswith("shortcuts."):
                return value[:64]
        if allowed is not None and value not in allowed:
            raise ValueError(f"invalid value for setting {path}: {value!r}")
        return value

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

        for section, defaults in DEFAULT_SETTINGS.items():
            if section in {"schemaVersion", "capabilities"}:
                continue
            raw_section = raw.get(section)
            normalized = dict(raw_section) if isinstance(raw_section, dict) else {}
            for key, default in defaults.items():
                path = f"{section}.{key}"
                candidate = normalized.get(key, default)
                try:
                    normalized[key] = LoomSettingsStore._validate_setting(path, candidate)
                except ValueError:
                    normalized[key] = deepcopy(default)
            data[section] = normalized
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


__all__ = ["DEFAULT_SETTINGS", "SETTINGS_UPDATE_PREFIX", "LoomSettingsStore"]

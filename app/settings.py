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
    "environment": {
        # Mirrors Codex's `shell_environment_policy` table. Defaults match
        # Codex's own: inherit the full parent environment and filter nothing,
        # which is what lets GH_TOKEN / GITHUB_TOKEN reach git and gh in a
        # spawned process.
        #
        # Seed for the child environment: all | core | none.
        "inherit": "all",
        # True (Codex's default) skips the *KEY* / *SECRET* / *TOKEN* denylist.
        # Set to false to turn that filtering back on.
        "ignoreDefaultExcludes": True,
        # Wildcard patterns (* and ?) matched case-insensitively against names.
        "exclude": [],
        # Operator-supplied name -> value pairs inserted after the excludes.
        # Distinct from the `env` argument on the model's exec tool, which is
        # screened separately and cannot introduce secret-shaped names.
        "set": {},
        # When non-empty, only names matching these patterns survive.
        "includeOnly": [],
        # Loom extension with no Codex counterpart: names that stay readable
        # even when `ignoreDefaultExcludes` is false. Lets an operator run the
        # denylist and still pass specific credentials through.
        "passThroughEnvVars": [],
    },
    "browser": {
        "mode": "auto",
        "cdpUrl": "",
        "preferredEngine": "edge",
        "persistSessions": True,
        "modelSelectsConnection": False,
        "allowPrivateNetworks": False,
    },
    "computer": {
        "verifyActions": True,
        "screenshotQuality": "balanced",
    },
    "memory": {
        "enabled": True,
        "autoExtract": True,
        "semanticAuto": True,
        "idleSeconds": 45,
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
    "environment.inherit": (str, {"all", "core", "none"}),
    "environment.ignoreDefaultExcludes": (bool, None),
    "environment.exclude": (list, None),
    "environment.set": (dict, None),
    "environment.includeOnly": (list, None),
    "environment.passThroughEnvVars": (list, None),
    "browser.mode": (str, {"auto", "local-launch", "cdp-attach", "extension"}),
    # Validated properly by the runtime, which is the only place that knows the
    # loopback rule. Storing it is not the same as accepting it.
    "browser.cdpUrl": (str, None),
    "browser.preferredEngine": (str, {"edge", "chrome", "system"}),
    "browser.persistSessions": (bool, None),
    "browser.modelSelectsConnection": (bool, None),
    "browser.allowPrivateNetworks": (bool, None),
    "computer.verifyActions": (bool, None),
    "computer.screenshotQuality": (str, {"fast", "balanced", "high"}),
    "memory.enabled": (bool, None),
    "memory.autoExtract": (bool, None),
    "memory.semanticAuto": (bool, None),
    "memory.idleSeconds": (int, range(0, 601)),
    "privacy.telemetry": (bool, None),
    "privacy.crashReports": (bool, None),
}

# Empty is a meaningful value for these: clearing the CDP endpoint is how the user
# says "no external browser", and rejecting it would strand a stale address in the
# stored settings after a switch back to local-launch.
_CLEARABLE_SETTING_PATHS = frozenset({"browser.cdpUrl"})

# Shell-environment settings holding env var names or wildcard patterns. They
# share one validator: a malformed entry here reaches every spawned process, so
# it is rejected at the settings boundary rather than at exec time.
_ENV_NAME_LIST_PATHS = frozenset(
    {
        "environment.exclude",
        "environment.includeOnly",
        "environment.passThroughEnvVars",
    }
)


class LoomSettingsStore:
    """Durable application settings shared by Loom desktop sessions.

    ``settings/set`` historically accepted only capability booleans. The
    ``__setting__:`` envelope remains supported for older desktop builds, while
    ``set_value`` is the typed path used by newer protocol clients.
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

    def replace(self, data: dict[str, Any]) -> dict[str, Any]:
        """Write back a previously read snapshot.

        A setting that the runtime then refuses to honour must not stay on disk,
        or the settings page would keep showing a browser connection that Loom is
        not actually using.
        """

        self._write(self._normalize(dict(data)))
        return self.snapshot()

    def set_value(self, path: str, value: Any) -> dict[str, Any]:
        key_path = str(path or "").strip()
        if key_path not in _ALLOWED_SETTING_PATHS:
            raise ValueError(f"unsupported setting path: {key_path}")
        normalized_value = self._validate_setting(key_path, value)
        section, key = key_path.split(".", 1)
        data = self.snapshot()
        bucket = dict(data.get(section) or {})
        bucket[key] = normalized_value
        data[section] = bucket
        self._write(data)
        return self.snapshot()

    def _set_enveloped_setting(self, payload_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid settings update envelope") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid settings update envelope")
        return self.set_value(
            str(payload.get("path") or "").strip(),
            payload.get("value"),
        )

    @staticmethod
    def _validate_setting(path: str, value: Any) -> Any:
        expected_type, allowed = _ALLOWED_SETTING_PATHS[path]
        if expected_type is int and isinstance(value, bool):
            raise ValueError(f"invalid value for setting {path}")
        if not isinstance(value, expected_type):
            raise ValueError(f"invalid value type for setting {path}")
        if expected_type is str:
            value = value.strip()
            if not value and path not in _CLEARABLE_SETTING_PATHS:
                raise ValueError(f"setting {path} cannot be empty")
            if path == "browser.cdpUrl":
                return value[:2000]
            if path == "appearance.codeFont":
                return value[:120]
            if path.startswith("shortcuts."):
                return value[:64]
        if expected_type is list and path in _ENV_NAME_LIST_PATHS:
            if not all(isinstance(item, str) for item in value):
                raise ValueError(f"setting {path} must be a list of strings")
            cleaned: list[str] = []
            for item in value:
                name = item.strip()
                if not name:
                    continue
                if len(name) > 256 or any(ch in name for ch in "\0="):
                    raise ValueError(f"setting {path} contains an invalid name: {item!r}")
                cleaned.append(name)
            return cleaned
        if path == "environment.set":
            # Operator-controlled, so unlike the model's exec `env` argument this
            # may carry a credential on purpose. Only shape is enforced.
            pairs: dict[str, str] = {}
            for raw_name, raw_value in value.items():
                name = str(raw_name).strip()
                if not name:
                    continue
                if len(name) > 256 or any(ch in name for ch in "\0="):
                    raise ValueError(f"setting {path} contains an invalid name: {raw_name!r}")
                if not isinstance(raw_value, str):
                    raise ValueError(f"setting {path} must map names to strings")
                if "\0" in raw_value or len(raw_value) > 64_000:
                    raise ValueError(f"setting {path} contains an invalid value for {name}")
                pairs[name] = raw_value
            return pairs
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

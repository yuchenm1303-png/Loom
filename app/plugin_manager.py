from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


_PLUGIN_DIRNAME = "plugins"
_PLUGIN_REGISTRY_FILENAME = "plugins.json"
_MANIFEST_NAMES = (
    "loom-plugin.json",
    "plugin.json",
    "package.json",
    "pyproject.toml",
    "SKILL.md",
)
_SAFE_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class PluginManagerError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _collapse(value: Any) -> str:
    return " ".join(str(value or "").split())


def _safe_name(value: Any, *, fallback: str = "plugin") -> str:
    text = _collapse(value)
    if not text:
        text = fallback
    text = _SAFE_SEGMENT_RE.sub("-", text).strip(".-_") or fallback
    if not re.match(r"^[A-Za-z0-9]", text):
        text = f"plugin-{text}"
    return text[:128]


def _validated_name(value: Any) -> str:
    name = _safe_name(value)
    if not _SAFE_PLUGIN_NAME_RE.fullmatch(name):
        raise PluginManagerError("plugin name must contain only letters, numbers, dot, underscore, and dash")
    return name


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".{uuid.uuid4().hex}.tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise PluginManagerError(f"invalid plugin registry JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PluginManagerError("plugin registry root must be an object")
    return data


def _read_toml_project(path: Path) -> dict[str, str]:
    try:
        import tomllib
    except Exception:  # pragma: no cover - Python >=3.11 always has tomllib here.
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    project = data.get("project") if isinstance(data, dict) else None
    if not isinstance(project, dict):
        return {}
    return {
        "name": _collapse(project.get("name")),
        "version": _collapse(project.get("version")),
        "description": _collapse(project.get("description")),
    }


def _read_markdown_title(path: Path) -> dict[str, str]:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if text.startswith("#"):
                return {"description": text.lstrip("#").strip()}
    except OSError:
        pass
    return {}


def _manifest_candidates(source: Path) -> Iterable[Path]:
    if source.is_file():
        yield source
        return
    if source.is_dir():
        for name in _MANIFEST_NAMES:
            candidate = source / name
            if candidate.is_file():
                yield candidate


def _read_manifest(source: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for candidate in _manifest_candidates(source):
        name = candidate.name.casefold()
        if name.endswith(".json"):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                manifest.update(
                    {
                        "name": _collapse(
                            payload.get("name")
                            or payload.get("id")
                            or payload.get("displayName")
                            or payload.get("display_name")
                        ),
                        "version": _collapse(payload.get("version")),
                        "description": _collapse(payload.get("description")),
                    }
                )
                break
        elif name == "pyproject.toml":
            manifest.update(_read_toml_project(candidate))
            break
        elif name == "skill.md":
            manifest.update(_read_markdown_title(candidate))
            break
    return {key: value for key, value in manifest.items() if value}


def _source_kind(source: str) -> str:
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https", "git", "ssh"}:
        return "remote"
    path = Path(source).expanduser()
    if path.is_dir():
        return "local-directory"
    if path.is_file():
        return "local-file"
    return "unknown"


@dataclass(frozen=True, slots=True)
class PluginRecord:
    name: str
    version: str = ""
    enabled: bool = True
    status: str = "Installed"
    source: str = ""
    description: str = ""
    kind: str = "registered"
    installedAt: str = ""
    updatedAt: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "enabled": bool(self.enabled),
            "status": self.status,
            "source": self.source,
            "description": self.description,
            "kind": self.kind,
            "installedAt": self.installedAt,
            "updatedAt": self.updatedAt,
        }


class PluginManager:
    """Small durable plugin registry for Loom's desktop settings page.

    v1 intentionally does not import or execute plugin code. It records local or
    remote plugin packages, reports them to the UI, and lets the user toggle or
    remove them. Runtime activation remains restart-scoped so later plugin
    loaders can consume the same registry without changing the App Server API.
    """

    def __init__(self, runtime_home: str | Path) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.root = self.runtime_home / _PLUGIN_DIRNAME
        self.registry_path = self.root / _PLUGIN_REGISTRY_FILENAME

    def _read_registry(self) -> dict[str, dict[str, Any]]:
        payload = _read_json(self.registry_path)
        plugins = payload.get("plugins") or {}
        if isinstance(plugins, list):
            converted: dict[str, dict[str, Any]] = {}
            for row in plugins:
                if isinstance(row, dict) and _collapse(row.get("name")):
                    converted[_safe_name(row.get("name"))] = dict(row)
            return converted
        if not isinstance(plugins, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, record in plugins.items():
            if isinstance(record, dict):
                result[_safe_name(record.get("name") or name)] = dict(record)
        return result

    def _write_registry(self, plugins: Mapping[str, Mapping[str, Any]]) -> None:
        _atomic_write_json(
            self.registry_path,
            {
                "schemaVersion": 1,
                "updatedAt": _utc_now(),
                "plugins": {name: dict(record) for name, record in sorted(plugins.items())},
            },
        )

    def _record_from_payload(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        source = _collapse(payload.get("source"))
        installed_at = _collapse(payload.get("installedAt"))
        updated_at = _collapse(payload.get("updatedAt")) or installed_at
        enabled = payload.get("enabled") is not False
        status = _collapse(payload.get("status")) or ("Enabled" if enabled else "Disabled")
        return PluginRecord(
            name=_validated_name(payload.get("name") or name),
            version=_collapse(payload.get("version")),
            enabled=enabled,
            status=status,
            source=source,
            description=_collapse(payload.get("description")),
            kind=_collapse(payload.get("kind")) or _source_kind(source) if source else "registered",
            installedAt=installed_at,
            updatedAt=updated_at,
        ).as_dict()

    def _discovered_plugins(self) -> dict[str, dict[str, Any]]:
        if not self.root.is_dir():
            return {}
        discovered: dict[str, dict[str, Any]] = {}
        now = _utc_now()
        for child in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if child.name == _PLUGIN_REGISTRY_FILENAME or child.name.startswith("."):
                continue
            manifest = _read_manifest(child)
            name = _safe_name(manifest.get("name") or child.stem or child.name)
            discovered[name] = PluginRecord(
                name=name,
                version=manifest.get("version", ""),
                enabled=True,
                status="Discovered",
                source=str(child),
                description=manifest.get("description", "Local Loom extension"),
                kind=_source_kind(str(child)),
                installedAt=now,
                updatedAt=now,
            ).as_dict()
        return discovered

    def list(self) -> list[dict[str, Any]]:
        registry = self._read_registry()
        merged = self._discovered_plugins()
        for name, payload in registry.items():
            merged[name] = self._record_from_payload(name, payload)
        return sorted(
            merged.values(),
            key=lambda record: (
                not bool(record.get("enabled", True)),
                str(record.get("name") or "").casefold(),
            ),
        )

    def install(self, source: str, *, upgrade: bool = False, enabled: bool = True) -> dict[str, Any]:
        source_text = _collapse(source)
        if not source_text:
            raise PluginManagerError("plugin source must not be empty")
        kind = _source_kind(source_text)
        path = Path(source_text).expanduser()
        manifest = _read_manifest(path) if kind.startswith("local-") else {}
        name = _validated_name(manifest.get("name") or path.stem or urlsplit(source_text).path.rsplit("/", 1)[-1] or source_text)

        registry = self._read_registry()
        if name in registry and not upgrade:
            raise PluginManagerError(f"plugin already installed: {name}")

        now = _utc_now()
        previous = registry.get(name, {})
        record = PluginRecord(
            name=name,
            version=manifest.get("version") or _collapse(previous.get("version")),
            enabled=bool(enabled),
            status="Enabled" if enabled else "Disabled",
            source=str(path.resolve()) if kind.startswith("local-") and path.exists() else source_text,
            description=manifest.get("description") or _collapse(previous.get("description")) or "Loom extension",
            kind=kind,
            installedAt=_collapse(previous.get("installedAt")) or now,
            updatedAt=now,
        ).as_dict()
        registry[name] = record
        self._write_registry(registry)
        return {"plugin": record, "installed": True, "activation": "runtime-restart"}

    def change(self, name: str, action: str, *, release: str = "") -> dict[str, Any]:
        plugin_name = _validated_name(name)
        operation = _collapse(action).casefold().replace("_", "-")
        registry = self._read_registry()
        if plugin_name not in registry:
            # A discovered local plugin can be toggled into the durable registry.
            discovered = self._discovered_plugins().get(plugin_name)
            if discovered is None:
                raise PluginManagerError(f"plugin is not installed: {plugin_name}")
            registry[plugin_name] = discovered

        record = dict(registry[plugin_name])
        now = _utc_now()
        if operation in {"enable", "enabled"}:
            record["enabled"] = True
            record["status"] = "Enabled"
        elif operation in {"disable", "disabled"}:
            record["enabled"] = False
            record["status"] = "Disabled"
        elif operation in {"remove", "uninstall", "delete"}:
            registry.pop(plugin_name, None)
            self._write_registry(registry)
            return {"plugin": {"name": plugin_name}, "removed": True, "activation": "runtime-restart"}
        elif operation in {"upgrade", "update"}:
            if release:
                record["version"] = _collapse(release)
            record["status"] = "Enabled" if record.get("enabled") is not False else "Disabled"
        else:
            raise PluginManagerError(f"unsupported plugin action: {action}")

        record["name"] = plugin_name
        record["updatedAt"] = now
        registry[plugin_name] = record
        self._write_registry(registry)
        return {"plugin": self._record_from_payload(plugin_name, record), "activation": "runtime-restart"}

    def purge(self) -> dict[str, Any]:
        if self.root.exists():
            shutil.rmtree(self.root)
        return {"purged": True}


__all__ = ["PluginManager", "PluginManagerError", "PluginRecord"]

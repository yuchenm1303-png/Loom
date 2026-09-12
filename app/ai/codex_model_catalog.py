from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_UNSUPPORTED_PRODUCT_LEVELS = {"ultra", "persistent"}


def _default_home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


def model_catalog_path() -> Path:
    """Return the Codex-compatible model catalog Loom should consult.

    ``LOOM_MODEL_CATALOG_JSON`` mirrors Codex's ``model_catalog_json`` concept
    for deployments that keep provider metadata outside ``LOOM_HOME``. When it
    is unset Loom looks for ``~/.loom/models.json``.
    """

    explicit = str(os.environ.get("LOOM_MODEL_CATALOG_JSON") or "").strip()
    return Path(explicit).expanduser().resolve() if explicit else _default_home() / "models.json"


def _model_slug(value: str) -> str:
    return str(value or "").strip().casefold().rsplit("/", 1)[-1]


def _catalog_models(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []
    return [dict(item) for item in raw_models if isinstance(item, dict)]


def _reasoning_levels(entry: dict[str, Any]) -> list[dict[str, Any]]:
    # Current Codex catalogs use supported_reasoning_levels. Accept the older
    # app-server spelling too so external catalogs do not have to fork for Loom.
    raw = entry.get("supported_reasoning_levels")
    if not isinstance(raw, list):
        raw = entry.get("supported_reasoning_efforts")
    if not isinstance(raw, list):
        return []

    levels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        effort = str(
            item.get("effort")
            or item.get("reasoning_effort")
            or item.get("value")
            or ""
        ).strip().casefold()
        if not effort or effort in seen or effort in _UNSUPPORTED_PRODUCT_LEVELS:
            continue
        seen.add(effort)
        levels.append(
            {
                "effort": effort,
                "description": str(item.get("description") or "").strip(),
                "advanced": bool(item.get("advanced")) or effort == "max",
            }
        )
    return levels


def catalog_reasoning_spec(model: str) -> dict[str, Any] | None:
    """Read one model's reasoning metadata from a Codex-compatible catalog.

    Catalog parsing is deliberately fail-closed. A malformed or unreadable file
    never breaks model selection and never invents reasoning capabilities; Loom
    simply falls back to its bundled provider knowledge.
    """

    target = _model_slug(model)
    if not target:
        return None
    path = model_catalog_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    for entry in _catalog_models(payload):
        slug = _model_slug(str(entry.get("slug") or entry.get("model") or entry.get("id") or ""))
        if not slug or slug != target:
            continue
        levels = _reasoning_levels(entry)
        if not levels:
            return None
        supported = {str(item["effort"]) for item in levels}
        default_value = str(
            entry.get("default_reasoning_level")
            or entry.get("default_reasoning_effort")
            or ""
        ).strip().casefold()
        if default_value in _UNSUPPORTED_PRODUCT_LEVELS or default_value not in supported:
            default_value = str(levels[0]["effort"])
        return {
            "default": default_value,
            "levels": levels,
            "source": f"Codex-compatible catalog: {path.name}",
        }
    return None


__all__ = ["catalog_reasoning_spec", "model_catalog_path"]

from __future__ import annotations

import json
import os
from pathlib import Path

from app.ai.codex_model_catalog import catalog_reasoning_spec


def _write(path: Path, *, default: str) -> None:
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "cached-reasoner",
                        "default_reasoning_level": default,
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "Low"},
                            {"effort": "high", "description": "High"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_catalog_is_parsed_once_until_file_signature_changes(tmp_path, monkeypatch) -> None:
    catalog = tmp_path / "models.json"
    _write(catalog, default="low")
    monkeypatch.setenv("LOOM_MODEL_CATALOG_JSON", str(catalog))

    calls = 0
    original = Path.read_text

    def counted_read_text(self: Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    first = catalog_reasoning_spec("cached-reasoner")
    second = catalog_reasoning_spec("cached-reasoner")

    assert first is not None
    assert second is not None
    assert first["default"] == "low"
    assert second["default"] == "low"
    assert calls == 1

    before = catalog.stat()
    _write(catalog, default="high")
    # Force a distinct signature even on filesystems with coarse timestamp
    # resolution while keeping the production cache dependent only on stat data.
    bumped = max(before.st_mtime_ns + 1_000_000, catalog.stat().st_mtime_ns + 1)
    os.utime(catalog, ns=(bumped, bumped))

    third = catalog_reasoning_spec("cached-reasoner")

    assert third is not None
    assert third["default"] == "high"
    assert calls == 2


def test_invalid_catalog_result_is_cached_but_recovers_after_rewrite(tmp_path, monkeypatch) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("LOOM_MODEL_CATALOG_JSON", str(catalog))

    calls = 0
    original = Path.read_text

    def counted_read_text(self: Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    assert catalog_reasoning_spec("cached-reasoner") is None
    assert catalog_reasoning_spec("cached-reasoner") is None
    assert calls == 1

    before = catalog.stat()
    _write(catalog, default="high")
    bumped = max(before.st_mtime_ns + 1_000_000, catalog.stat().st_mtime_ns + 1)
    os.utime(catalog, ns=(bumped, bumped))

    recovered = catalog_reasoning_spec("cached-reasoner")

    assert recovered is not None
    assert recovered["default"] == "high"
    assert calls == 2

from __future__ import annotations

import json

from app.ai.codex_model_catalog import catalog_reasoning_spec, model_catalog_path
from app.ai.reasoning_catalog import reasoning_capability


def _write_catalog(path, models) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"models": models}), encoding="utf-8")


def test_default_loom_catalog_reads_codex_reasoning_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.delenv("LOOM_MODEL_CATALOG_JSON", raising=False)
    _write_catalog(
        tmp_path / "models.json",
        [
            {
                "slug": "glm-5.1",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Quick"},
                    {"effort": "medium", "description": "Balanced"},
                    {"effort": "high", "description": "Deep"},
                    {"effort": "max", "description": "Maximum"},
                ],
            }
        ],
    )

    capability = reasoning_capability(
        model="vendor/glm-5.1",
        adapter="openai-compatible",
        base_url="https://relay.example.invalid/v1",
    )

    assert model_catalog_path() == tmp_path / "models.json"
    assert capability is not None
    assert capability["defaultValue"] == "medium"
    assert capability["source"] == "Codex-compatible catalog: models.json"
    assert [item["value"] for item in capability["options"]] == [
        "low", "medium", "high", "max"
    ]
    assert capability["options"][-1]["advanced"] is True
    assert capability["options"][0]["description"] == "Quick"


def test_catalog_supports_future_custom_effort_without_source_change(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.delenv("LOOM_MODEL_CATALOG_JSON", raising=False)
    _write_catalog(
        tmp_path / "models.json",
        [
            {
                "slug": "future-reasoner",
                "default_reasoning_level": "turbo",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "turbo", "description": "Provider turbo mode"},
                ],
            }
        ],
    )

    capability = reasoning_capability(
        model="future-reasoner",
        adapter="openai-compatible",
    )

    assert capability is not None
    assert capability["defaultValue"] == "turbo"
    assert [item["value"] for item in capability["options"]] == ["low", "turbo"]
    assert capability["options"][1]["label"] == "Turbo"


def test_catalog_filters_product_only_levels_until_loom_supports_their_semantics(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.delenv("LOOM_MODEL_CATALOG_JSON", raising=False)
    _write_catalog(
        tmp_path / "models.json",
        [
            {
                "slug": "third-party",
                "default_reasoning_level": "ultra",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "max", "description": "Maximum"},
                    {"effort": "ultra", "description": "Delegating mode"},
                    {"effort": "persistent", "description": "Persistent mode"},
                ],
            }
        ],
    )

    capability = reasoning_capability(model="third-party", adapter="openai-compatible")

    assert capability is not None
    assert capability["defaultValue"] == "low"
    assert [item["value"] for item in capability["options"]] == ["low", "max"]


def test_explicit_catalog_path_overrides_default_home(tmp_path, monkeypatch) -> None:
    default_home = tmp_path / "home"
    explicit = tmp_path / "provider" / "codex-models.json"
    monkeypatch.setenv("LOOM_HOME", str(default_home))
    monkeypatch.setenv("LOOM_MODEL_CATALOG_JSON", str(explicit))
    _write_catalog(
        explicit,
        [
            {
                "slug": "custom-reasoner",
                "default_reasoning_effort": "high",
                "supported_reasoning_efforts": [
                    {"reasoning_effort": "medium", "description": "Medium"},
                    {"reasoning_effort": "high", "description": "High"},
                ],
            }
        ],
    )

    spec = catalog_reasoning_spec("custom-reasoner")
    capability = reasoning_capability(model="custom-reasoner", adapter="openai-compatible")

    assert model_catalog_path() == explicit
    assert spec is not None
    assert capability is not None
    assert capability["defaultValue"] == "high"
    assert [item["value"] for item in capability["options"]] == ["medium", "high"]


def test_invalid_catalog_fails_closed_and_builtin_catalog_still_works(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.delenv("LOOM_MODEL_CATALOG_JSON", raising=False)
    (tmp_path / "models.json").write_text("{not-json", encoding="utf-8")

    unknown = reasoning_capability(model="qwen-plus", adapter="openai-compatible")
    builtin = reasoning_capability(model="gpt-5.6-sol", adapter="openai")

    assert unknown is None
    assert builtin is not None
    assert builtin["defaultValue"] == "low"

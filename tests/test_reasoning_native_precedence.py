from __future__ import annotations

import json

from app.ai.reasoning_catalog import reasoning_capability


def _values(capability: dict[str, object]) -> list[str]:
    return [str(option["value"]) for option in capability["options"]]  # type: ignore[index]


def test_minimax_native_thinking_wins_over_generic_codex_catalog(tmp_path, monkeypatch) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "MiniMax-M3",
                        "default_reasoning_level": "high",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "catalog low"},
                            {"effort": "high", "description": "catalog high"},
                            {"effort": "max", "description": "catalog max"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOM_MODEL_CATALOG_JSON", str(catalog))

    capability = reasoning_capability(
        model="MiniMax-M3",
        adapter="openai-compatible",
        base_url="https://api.minimaxi.com/v1",
    )

    assert capability is not None
    assert capability["kind"] == "minimax-thinking"
    assert capability["defaultValue"] == "adaptive"
    assert _values(capability) == ["disabled", "adaptive"]
    assert capability["source"] == "MiniMax M3 hosted API"


def test_generic_catalog_still_overrides_bundled_openai_fallback(tmp_path, monkeypatch) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-5.6-sol",
                        "default_reasoning_level": "high",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "custom low"},
                            {"effort": "high", "description": "custom high"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOM_MODEL_CATALOG_JSON", str(catalog))

    capability = reasoning_capability(
        model="gpt-5.6-sol",
        adapter="openai-compatible",
        base_url="https://relay.example.invalid/v1",
    )

    assert capability is not None
    assert capability["kind"] == "openai-effort"
    assert capability["defaultValue"] == "high"
    assert _values(capability) == ["low", "high"]
    assert str(capability["source"]).startswith("Codex-compatible catalog:")

from __future__ import annotations

import types
from pathlib import Path

from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime
from app.agent_runtime.computer_ufo_driver import UfoDriverConfig, UfoWindowsDriver


def _driver_config(tmp_path: Path) -> UfoDriverConfig:
    return UfoDriverConfig(
        install_root=tmp_path / "ufo",
        source_root=tmp_path / "ufo" / "src",
        python=tmp_path / "python.exe",
        sidecar=tmp_path / "ufo_sidecar.py",
        api_type="openai",
        api_base="",
        api_key="",
        api_model="",
    )


def test_ufo_driver_inherits_openai_compatible_active_model(tmp_path: Path):
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver = UfoWindowsDriver(_driver_config(tmp_path))
    runtime.platform = types.SimpleNamespace(
        _loom_model_connection={
            "provider": "openai-compatible",
            "base_url": "https://example.test/v1/chat/completions",
            "model": "vision-model",
            "api_key": "runtime-secret",
            "vision": True,
        }
    )

    ComputerDriverRuntime._sync_driver_model_from_platform(runtime)

    config = runtime.computer_driver.config
    assert config.api_type == "openai"
    assert config.api_base == "https://example.test/v1"
    assert config.api_key == "runtime-secret"
    assert config.api_model == "vision-model"


def test_ufo_driver_inherits_openai_active_model_default_base(tmp_path: Path):
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver = UfoWindowsDriver(_driver_config(tmp_path))
    runtime.platform = types.SimpleNamespace(
        _loom_model_connection={
            "provider": "openai",
            "base_url": "",
            "model": "gpt-4o",
            "api_key": "runtime-secret",
            "vision": True,
        }
    )

    ComputerDriverRuntime._sync_driver_model_from_platform(runtime)

    config = runtime.computer_driver.config
    assert config.api_type == "openai"
    assert config.api_base == "https://api.openai.com/v1"
    assert config.api_key == "runtime-secret"
    assert config.api_model == "gpt-4o"


def test_ufo_driver_does_not_inherit_non_vision_model(tmp_path: Path):
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver = UfoWindowsDriver(_driver_config(tmp_path))
    runtime.platform = types.SimpleNamespace(
        _loom_model_connection={
            "provider": "openai-compatible",
            "base_url": "https://example.test/v1",
            "model": "text-only-model",
            "api_key": "runtime-secret",
            "vision": False,
        }
    )

    ComputerDriverRuntime._sync_driver_model_from_platform(runtime)

    config = runtime.computer_driver.config
    assert config.api_key == ""
    assert config.api_model == ""

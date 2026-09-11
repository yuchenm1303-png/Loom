from __future__ import annotations

from pathlib import Path

from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.computer_ufo_driver import (
    UFO_COMMIT,
    UFO_TAG,
    UfoDriverConfig,
    UfoWindowsDriver,
    _normalize_base_url,
)
from app.agent_runtime.mcp_configured_runtime import ConfiguredMCPRuntime
from app.agent_runtime.ufo_sidecar import _hud_point, _safe_parameters


def test_default_runtime_composes_mature_computer_driver_before_mcp():
    assert issubclass(ConfiguredMCPRuntime, ComputerDriverRuntime)


def test_ufo_pin_is_explicit_and_stable():
    assert UFO_TAG == "v3.0.8"
    assert UFO_COMMIT == "96983c73ed09e884a5f1d7ff8936c953b234b684"


def test_openai_compatible_base_url_is_normalized():
    assert _normalize_base_url("https://example.test/v1/chat/completions") == "https://example.test/v1"
    assert _normalize_base_url("https://example.test/v1/responses/") == "https://example.test/v1"


def test_ufo_status_is_side_effect_free_when_not_installed(tmp_path: Path):
    install = tmp_path / "ufo"
    config = UfoDriverConfig(
        install_root=install,
        source_root=install / "src",
        python=install / ".venv" / "Scripts" / "python.exe",
        sidecar=tmp_path / "ufo_sidecar.py",
        api_type="openai",
        api_base="https://api.openai.com/v1",
        api_key="secret",
        api_model="vision-model",
    )
    driver = UfoWindowsDriver(config)
    status = dict(driver.status())

    assert status["sidecar_alive"] is False
    assert status["running"] is False
    assert status["api_key_configured"] is True
    assert "api_key" not in status


def test_sidecar_redacts_text_before_driver_events():
    safe = _safe_parameters(
        "set_edit_text",
        {"id": "4", "name": "Message", "text": "super secret text"},
    )
    assert safe["text"] == "[TRANSIENT_TEXT]"
    assert safe["text_length"] == len("super secret text")
    assert "super secret text" not in repr(safe)


def test_runtime_redacts_provider_errors_and_text_before_persistence():
    safe = _safe_driver_data(
        {
            "result": {"status": "failure", "error": "typed secret leaked in exception"},
            "parameters": {"text": "typed secret", "text_length": 12},
            "window": {"title": "WeChat", "rectangle": {"x": 10}},
        }
    )
    assert safe["result"]["error"] == "[REDACTED_DRIVER_DATA]"
    assert safe["parameters"]["text"] == "[REDACTED_DRIVER_DATA]"
    assert safe["parameters"]["text_length"] == 12
    assert safe["window"]["title"] == "WeChat"


def test_hud_point_uses_selected_application_window_not_whole_screen(monkeypatch):
    from app.agent_runtime import ufo_sidecar

    monkeypatch.setattr(
        ufo_sidecar,
        "_virtual_screen_bounds",
        lambda: {"x": 0, "y": 0, "width": 2000, "height": 1000},
    )
    point = _hud_point(
        {"rectangle": {"x": 1000, "y": 200, "width": 500, "height": 400}},
        "click_on_coordinates",
        {"x": 0.5, "y": 0.5},
    )

    assert point is not None
    assert point["screen_x"] == 1250
    assert point["screen_y"] == 400
    assert point["x_norm"] == 0.625
    assert point["y_norm"] == 0.4

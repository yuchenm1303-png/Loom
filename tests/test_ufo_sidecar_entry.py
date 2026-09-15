from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

from app.agent_runtime import ufo_sidecar_entry as entry

# Importing the runtime entrypoint intentionally projects patched callables onto
# the shared ufo_sidecar module. These unit tests only exercise entrypoint helper
# behavior, so restore the shared core immediately after collection; otherwise
# importing this test module changes what unrelated cold-start contract tests see.
entry.core._bootstrap_ufo = entry._ORIGINAL_BOOTSTRAP
entry.core._run_task = entry._ORIGINAL_RUN_TASK


def test_entrypoint_unit_tests_do_not_leak_core_patch_state():
    assert entry.core._bootstrap_ufo is entry._ORIGINAL_BOOTSTRAP
    assert entry.core._run_task is entry._ORIGINAL_RUN_TASK


def test_window_match_prefers_explicit_existing_title():
    windows = [
        {"title": "Loom", "process_name": "Loom.exe", "handle": 1},
        {"title": "微信", "process_name": "WeChat.exe", "handle": 2},
    ]
    chosen = entry._choose_focus_candidate("打开微信找到和妈妈的对话框", windows)
    assert chosen is windows[1]


def test_window_match_refuses_an_ambiguous_tie():
    windows = [
        {"title": "Report", "process_name": "alpha.exe", "handle": 1},
        {"title": "Report", "process_name": "beta.exe", "handle": 2},
    ]
    assert entry._choose_focus_candidate("open Report", windows) is None


def test_desktop_context_tells_ufo_to_reuse_and_reanchor():
    text = entry._desktop_context(
        "Open WeChat",
        [{"handle": 0x30EF2, "title": "WeChat", "process_name": "WeChat.exe", "minimized": True}],
    )
    assert "WeChat" in text
    assert "do not press Win+D" in text
    assert "select_application_window" in text
    assert "refresh/select the current window" in text


def test_settings_capture_profile_is_read_from_loom_home(monkeypatch, tmp_path: Path):
    root = tmp_path / "drivers" / "ufo" / "3.0.8" / "src"
    root.mkdir(parents=True)
    (tmp_path / "settings.json").write_text(
        json.dumps({"computer": {"screenshotQuality": "fast"}}), encoding="utf-8"
    )
    monkeypatch.delenv("LOOM_UFO_SCREENSHOT_QUALITY", raising=False)
    assert entry._resolve_capture_profile(root) == "fast"


def test_environment_capture_profile_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOOM_UFO_SCREENSHOT_QUALITY", "high")
    assert entry._resolve_capture_profile(tmp_path) == "high"


def test_balanced_model_encoding_is_jpeg_and_downscaled():
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (2560, 1600), (120, 130, 140))
    url = entry._encode_image_for_model(image, "balanced")
    assert url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    decoded = Image.open(io.BytesIO(raw))
    assert decoded.format == "JPEG"
    assert decoded.width * decoded.height <= 2_500_000
    assert abs((decoded.width / decoded.height) - (2560 / 1600)) < 0.01


def test_lossless_remains_available_for_diagnostics():
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (320, 200), (10, 20, 30))
    url = entry._encode_image_for_model(image, "lossless")
    assert url.startswith("data:image/png;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")

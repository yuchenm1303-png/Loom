"""Model bridge JSON must roundtrip on Windows' non-Unicode redirected streams."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", ["loom_model_bridge", "loom_model_admin", "loom_ant_ling_bridge"])
def test_model_bridge_payload_is_codepage_independent(module):
    script = (
        "import sys, json; "
        f"from {module} import _write; "
        "payload = {'models': [{'name': '新建会话 — 模型 🧪'} for _ in range(3500)]}; "
        "_write(payload)"
    )
    env = os.environ.copy()
    env["PYTHONUTF8"] = "0"
    env["PYTHONIOENCODING"] = "cp1252:strict"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert all(value < 128 for value in result.stdout)
    decoded = json.loads(result.stdout.decode("ascii"))
    assert len(decoded["models"]) == 3500
    assert decoded["models"][-1]["name"] == "新建会话 — 模型 🧪"

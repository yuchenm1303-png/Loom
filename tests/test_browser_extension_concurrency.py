from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_extension_concurrent_controllers_are_isolated():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the extension runtime test")
    script = Path(__file__).with_name("browser_extension_concurrency.cjs")
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr

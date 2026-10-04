"""Opt-in real browser regression, separate from mocked protocol tests."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("LOOM_BROWSER_NATIVE_ACCEPTANCE") != "1", reason="requires Playwright and installed Edge")
def test_real_browser_editing_and_event_delivery():
    node = shutil.which("node")
    assert node, "Node.js is required for real browser acceptance"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("browser_native_acceptance.cjs"))],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

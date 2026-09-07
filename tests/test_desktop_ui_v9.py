from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.desktop_ui import LoomDesktopWindow
from app.desktop_ui_v9 import _ACTIVITY_STYLE, _TRANSCRIPT_STYLE, _UI_TYPOGRAPHY_QSS


def test_active_desktop_entrypoint_uses_readability_typography_layer():
    assert LoomDesktopWindow.__module__ == "app.desktop_ui_v9"


def test_v9_typography_contract_prioritizes_readability_and_cjk_fallbacks():
    assert '"Segoe UI Variable Text"' in _UI_TYPOGRAPHY_QSS
    assert '"Microsoft YaHei UI"' in _UI_TYPOGRAPHY_QSS
    assert "QLabel#threadItemTitle { font-size:14px" in _UI_TYPOGRAPHY_QSS
    assert "font-size:16px" in _TRANSCRIPT_STYLE
    assert "line-height:1.66" in _TRANSCRIPT_STYLE
    assert "'Microsoft YaHei UI'" in _TRANSCRIPT_STYLE
    assert "font-size:12px" in _ACTIVITY_STYLE

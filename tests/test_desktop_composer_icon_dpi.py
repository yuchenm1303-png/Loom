from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.desktop import composer_polish


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_scaled_geometry_uses_logical_size_on_qt_68_plus(monkeypatch):
    monkeypatch.setattr(composer_polish, "_QT_SCALED_PIXMAP_SIZE_IS_PHYSICAL", False)

    logical, physical = composer_polish._scaled_pixmap_geometry(QSize(16, 16), 1.5)

    assert logical == QSize(16, 16)
    assert physical == QSize(24, 24)


def test_scaled_geometry_recovers_logical_size_on_qt_67(monkeypatch):
    monkeypatch.setattr(composer_polish, "_QT_SCALED_PIXMAP_SIZE_IS_PHYSICAL", True)

    # Qt < 6.8 passed the already device-scaled size into scaledPixmap().
    logical, physical = composer_polish._scaled_pixmap_geometry(QSize(24, 24), 1.5)

    assert logical == QSize(16, 16)
    assert physical == QSize(24, 24)


def test_icon_engine_returns_dpr_tagged_backing_store(qt_app, monkeypatch):
    monkeypatch.setattr(composer_polish, "_QT_SCALED_PIXMAP_SIZE_IS_PHYSICAL", False)
    engine = composer_polish._ComposerIconEngine("workspace", "#ffffff")

    pixmap = engine.scaledPixmap(QSize(16, 16), QIcon.Mode.Normal, QIcon.State.Off, 1.5)

    assert pixmap.width() == 24
    assert pixmap.height() == 24
    assert pixmap.devicePixelRatio() == pytest.approx(1.5)
    assert pixmap.deviceIndependentSize().width() == pytest.approx(16.0)
    assert pixmap.deviceIndependentSize().height() == pytest.approx(16.0)


def test_composer_icon_is_vector_engine_backed(qt_app):
    icon = composer_polish._composer_icon("permission", "#d3b466")

    # Rendering through QIcon must stay valid without adding any fixed 16px source pixmap.
    pixmap = icon.pixmap(QSize(16, 16), 1.5, QIcon.Mode.Normal, QIcon.State.Off)

    assert not pixmap.isNull()
    assert pixmap.devicePixelRatio() >= 1.0

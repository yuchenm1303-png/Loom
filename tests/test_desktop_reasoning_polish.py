from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.message_presentation import MessageWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(app: QApplication, *, loops: int = 70) -> None:
    for _ in range(loops):
        app.processEvents()
        time.sleep(0.005)


def test_reasoning_disclosure_has_compact_product_surface(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("<think>先确认环境，再执行需要的命令。</think>好的。")

    reasoning = message.reasoning
    assert reasoning is not None
    assert reasoning.isHidden() is False
    assert reasoning.toggle.minimumHeight() >= 30
    assert reasoning.toggle.property("expanded") is False
    assert "background:#151821" in reasoning.styleSheet()
    assert 'reasoningToggle[expanded="true"]' in reasoning.styleSheet()
    assert "border-left:2px solid #625b86" in reasoning.styleSheet()

    reasoning.toggle.click()
    assert reasoning.toggle.property("expanded") is True
    assert reasoning.body.isHidden() is False
    assert reasoning.toggle.toolTip() == "Hide thought process"

    reasoning.toggle.click()
    assert reasoning.toggle.property("expanded") is False
    assert reasoning.body.isHidden() is True
    assert reasoning.toggle.toolTip() == "Show thought process"
    message.close()


def test_reasoning_disclosure_animates_height_opacity_and_chevron(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    message = MessageWidget("assistant")
    message.resize(720, 240)
    message.set_text(
        "<think>先检查 C 盘空间，再查看占用较大的目录，最后汇总给用户。\n"
        "这里需要保持过程清楚，但不要抢占最终回答的视觉层级。</think>完成。"
    )
    message.show()
    app.processEvents()

    reasoning = message.reasoning
    assert reasoning is not None
    reasoning.toggle.click()

    assert reasoning._animation is not None
    assert reasoning.toggle._animation is not None
    assert reasoning.toggle.property("expanded") is True
    assert reasoning.body.isHidden() is False
    assert reasoning.body.maximumHeight() < 16777215

    _settle(app)
    assert reasoning._expanded is True
    assert reasoning.body.isHidden() is False
    assert reasoning.body.maximumHeight() == 16777215
    assert reasoning.toggle.angle == 90.0

    reasoning.toggle.click()
    assert reasoning._animation is not None
    assert reasoning.toggle.property("expanded") is False
    _settle(app)
    assert reasoning._expanded is False
    assert reasoning.body.isHidden() is True
    assert reasoning.toggle.angle == 0.0
    message.close()

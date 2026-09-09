from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import MessageWidget
from app.desktop.transcript_disclosure import AnimatedReveal, ReasoningDisclosure


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(app: QApplication, *, loops: int = 70) -> None:
    for _ in range(loops):
        app.processEvents()
        time.sleep(0.005)


def test_reasoning_disclosure_uses_quiet_inline_surface(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("<think>先确认环境，再执行需要的命令。</think>好的。")

    reasoning = message.reasoning
    assert isinstance(reasoning, ReasoningDisclosure)
    assert isinstance(reasoning.reveal, AnimatedReveal)
    assert reasoning.isHidden() is False
    assert reasoning.toggle.minimumHeight() >= 26
    assert not bool(reasoning.toggle.property("expanded"))
    assert "background:transparent" in reasoning.styleSheet()
    assert "border:none" in reasoning.styleSheet()
    assert 'reasoningToggle[expanded="true"]' in reasoning.styleSheet()
    assert "border-left:2px solid #3d3a55" in reasoning.styleSheet()

    reasoning.toggle.click()
    assert reasoning.toggle.property("expanded") is True
    assert reasoning.reveal.progress == 1.0
    assert reasoning.toggle.toolTip() == "Hide thought process"

    reasoning.toggle.click()
    assert not bool(reasoning.toggle.property("expanded"))
    assert reasoning.reveal.progress == 0.0
    assert reasoning.toggle.toolTip() == "Show thought process"
    message.close()


def test_reasoning_disclosure_has_one_animation_owner(app, monkeypatch):
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
    assert isinstance(reasoning, ReasoningDisclosure)
    reasoning.toggle.click()
    assert reasoning.reveal._animation is not None
    assert not hasattr(reasoning, "_animation")
    assert not hasattr(reasoning.toggle, "_animation")

    _settle(app)
    assert reasoning.reveal._animation is None
    assert reasoning.reveal.progress == pytest.approx(1.0)
    assert reasoning.toggle.progress == pytest.approx(1.0)

    reasoning.toggle.click()
    _settle(app)
    assert reasoning.reveal._animation is None
    assert reasoning.reveal.progress == pytest.approx(0.0)
    assert reasoning.toggle.progress == pytest.approx(0.0)
    message.close()

from __future__ import annotations

from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator


class InvokeWrapper:
    def __init__(self) -> None:
        self.invoked = 0
        self.click_input_calls = 0

    def invoke(self) -> None:
        self.invoked += 1

    def click_input(self, **kwargs) -> None:
        self.click_input_calls += 1


class PhysicalOnlyWrapper:
    def __init__(self) -> None:
        self.click_input_calls = 0

    def click_input(self, **kwargs) -> None:
        self.click_input_calls += 1


def test_native_click_reports_delivered_only_for_uia_invoke():
    operator = object.__new__(PyWinAutoWindowsOperator)
    wrapper = InvokeWrapper()

    assert operator._native_click(wrapper, double=False, right=False) == "delivered"
    assert wrapper.invoked == 1
    assert wrapper.click_input_calls == 0


def test_click_input_is_not_mislabeled_as_native_uia():
    operator = object.__new__(PyWinAutoWindowsOperator)
    wrapper = PhysicalOnlyWrapper()

    assert operator._native_click(wrapper, double=False, right=False) == "not_delivered"
    assert operator._native_click(wrapper, double=True, right=False) == "not_delivered"
    assert operator._native_click(wrapper, double=False, right=True) == "not_delivered"
    assert wrapper.click_input_calls == 0

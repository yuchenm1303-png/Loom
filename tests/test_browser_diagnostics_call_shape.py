"""Guard the diagnostics call shape in the browser backends.

Every browser action - navigate, click, type, scroll, back, refresh, tab switch -
funnels through BrowserUseBackend._dispatch. That method logged its progress with
``self._log("...", event=event_name)``, but ``_log`` and the diagnostics sink both
take the record name as their first positional parameter. Python raises TypeError
for the duplicate argument before either body runs, so the whole browser backend
failed on its first dispatch regardless of whether diagnostics were enabled.

A unit test on one call site would not have caught it, so this checks the shape of
every such call in the browser modules.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.agent_runtime import browser_use_backend

_LOGGING_CALLS = {"_log", "event", "trace"}


def _browser_modules() -> list[Path]:
    root = Path(inspect.getfile(browser_use_backend)).parent
    return sorted(root.glob("browser*.py"))


def test_browser_modules_never_shadow_the_diagnostics_record_name():
    offenders: list[str] = []
    for path in _browser_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if getattr(node.func, "attr", None) not in _LOGGING_CALLS:
                continue
            for keyword in node.keywords:
                if keyword.arg == "event":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "these calls pass event= alongside a positional record name, which raises "
        f"TypeError at call time: {offenders}"
    )


def test_log_accepts_the_field_name_dispatch_actually_uses():
    class _Sink:
        def __init__(self):
            self.records: list[tuple[str, dict]] = []

        def event(self, event: str, **fields):
            self.records.append((event, fields))

    sink = _Sink()

    class _Backend(browser_use_backend.BrowserUseBackend):
        backend_name = "test"

        def __init__(self):
            self.diagnostics = sink

        def _connection_mode(self):
            return "test"

    backend = _Backend()
    backend._log("browser_use.event.dispatch.started", browser_event="NavigateToUrlEvent")

    assert sink.records[0][0] == "browser_use.event.dispatch.started"
    assert sink.records[0][1]["browser_event"] == "NavigateToUrlEvent"


def test_log_still_refuses_a_colliding_keyword():
    class _Backend(browser_use_backend.BrowserUseBackend):
        backend_name = "test"

        def __init__(self):
            self.diagnostics = None

    backend = _Backend()
    with pytest.raises(TypeError):
        backend._log("browser_use.event.dispatch.started", event="NavigateToUrlEvent")

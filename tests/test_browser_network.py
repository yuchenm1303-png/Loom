"""Live network inspection, and why it is not built on HAR.

browser-use can record a HAR, but it writes the file only on BrowserStopEvent,
so it cannot answer the question an agent actually has - "did that request just
fail?" - while the session is still open. This captures CDP network events into
a bounded ring instead.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_backend import BrowserUseSessionBackend
from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_use_backend import _NETWORK_LOG_LIMIT
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


@pytest.fixture
def runtime(tmp_path):
    built = BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    yield built
    built.close()


def _bare_backend():
    instance = object.__new__(BrowserUseSessionBackend)
    instance.options = BrowserLaunchOptions(headless=True)
    instance.diagnostics = None
    instance.action_timeout_seconds = 30.0
    return instance


def test_the_request_log_is_a_bounded_ring():
    """A page can issue thousands of requests; recent ones are what is asked about."""

    backend = _bare_backend()
    log = backend._network_log()
    for index in range(_NETWORK_LOG_LIMIT + 50):
        log.append({"request_id": str(index)})

    entries = backend.network_entries()
    assert len(entries) == _NETWORK_LOG_LIMIT
    assert entries[-1]["request_id"] == str(_NETWORK_LOG_LIMIT + 49)
    assert entries[0]["request_id"] == str(50)


def test_entries_are_copied_so_a_caller_cannot_corrupt_the_log():
    backend = _bare_backend()
    backend._network_log().append({"request_id": "a", "status": 200})

    snapshot = backend.network_entries()
    snapshot[0]["status"] = 500

    assert backend.network_entries()[0]["status"] == 200


def test_clear_empties_the_log():
    backend = _bare_backend()
    backend._network_log().append({"request_id": "a"})
    backend.network_clear()
    assert backend.network_entries() == []


def test_a_missing_request_id_is_refused_before_reaching_the_browser():
    backend = _bare_backend()
    with pytest.raises(ValueError, match="must not be empty"):
        backend.network_body("   ")


def _session_with_log(runtime, tmp_path, entries):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def state(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def network_entries(self):
            return [dict(item) for item in entries]

        def network_start(self):
            return {"capturing": True, "entries": len(entries)}

        def network_stop(self):
            return {"capturing": False, "entries": len(entries)}

        def network_clear(self):
            entries.clear()

        def network_body(self, request_id):
            if request_id == "gone":
                return {"ok": False, "error": "No resource with given identifier"}
            return {"ok": True, "body": "hello", "truncated": False, "base64_encoded": False}

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-net", turn_id="t-1", workspace=workspace)
    browser_id = runtime.tools.get("browser_open").handler(context, {}).data["browser_id"]
    return context, browser_id


SAMPLE = [
    {"request_id": "1", "method": "GET", "url": "https://x.test/app.css", "status": 200, "bytes": 10},
    {"request_id": "2", "method": "GET", "url": "https://x.test/api/items", "status": 500, "bytes": 20},
    {"request_id": "3", "method": "POST", "url": "https://x.test/api/save", "status": 204, "bytes": 0},
    {"request_id": "4", "method": "GET", "url": "https://x.test/img.png", "status": None, "error": "failed"},
]


def test_failures_only_selects_error_statuses_and_transport_failures(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, list(SAMPLE))

    result = runtime.tools.get("browser_network").handler(
        context, {"browser_id": browser_id, "action": "list", "failures_only": True}
    )

    ids = [row["request_id"] for row in result.data["requests"]]
    # 500 and the transport failure, but not 200 or 204.
    assert ids == ["2", "4"]


def test_url_filter_is_case_insensitive(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, list(SAMPLE))

    result = runtime.tools.get("browser_network").handler(
        context, {"browser_id": browser_id, "action": "list", "url_contains": "API"}
    )

    assert [row["request_id"] for row in result.data["requests"]] == ["2", "3"]


def test_list_keeps_the_most_recent_entries_under_a_limit(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, list(SAMPLE))

    result = runtime.tools.get("browser_network").handler(
        context, {"browser_id": browser_id, "action": "list", "limit": 2}
    )

    assert [row["request_id"] for row in result.data["requests"]] == ["3", "4"]


def test_an_expired_body_is_a_clear_result_rather_than_an_error(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, list(SAMPLE))

    result = runtime.tools.get("browser_network").handler(
        context, {"browser_id": browser_id, "action": "body", "request_id": "gone"}
    )

    assert result.ok is False
    assert "no longer available" in result.content


def test_body_requires_a_request_id(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, list(SAMPLE))

    with pytest.raises(ValueError, match="requires request_id"):
        runtime.tools.get("browser_network").handler(
            context, {"browser_id": browser_id, "action": "body"}
        )


def test_the_tool_says_that_nothing_is_recorded_before_start(runtime, tmp_path):
    context, browser_id = _session_with_log(runtime, tmp_path, [])

    result = runtime.tools.get("browser_network").handler(
        context, {"browser_id": browser_id, "action": "list"}
    )

    # The model has to know capture is opt-in, or it reads an empty log as
    # "the page made no requests".
    assert "until action=start" in result.content
    assert "action=start" in runtime.tools.get("browser_network").description


def test_the_description_warns_that_bodies_expire(runtime):
    description = runtime.tools.get("browser_network").description.casefold()
    assert "briefly" in description

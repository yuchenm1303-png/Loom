"""Device emulation and permission grants.

A site that gates on user agent, a responsive layout, and a permission prompt
that blocks until answered were all out of reach before this.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.browser_tools import (
    _CDP_PERMISSIONS,
    _validated_geolocation,
    _validated_permissions,
    _validated_viewport,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


@pytest.fixture
def session(tmp_path):
    runtime = BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    calls: list[dict] = []

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            return BrowserPageState(url="https://x.test/", title="X")

        def state(self):
            return BrowserPageState(url="https://x.test/", title="X")

        def emulate(self, **kwargs):
            calls.append(kwargs)
            applied = [key for key, value in kwargs.items() if value]
            return {"applied": applied}

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-1", turn_id="t-1", workspace=workspace)
    browser_id = runtime.tools.get("browser_open").handler(context, {}).data["browser_id"]
    yield runtime, context, browser_id, calls
    runtime.close()


def _emulate(runtime, context, browser_id, **kwargs):
    return runtime.tools.get("browser_emulate").handler(
        context, {"browser_id": browser_id, **kwargs}
    )


class TestViewportValidation:
    def test_defaults_are_filled_in(self):
        assert _validated_viewport({"width": 390, "height": 844}) == {
            "width": 390,
            "height": 844,
            "device_scale_factor": 1.0,
            "mobile": False,
        }

    def test_absent_means_unchanged(self):
        assert _validated_viewport(None) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"width": 100}, "integer width and height"),
            ({"width": 0, "height": 100}, "within 1..10000"),
            ({"width": 20000, "height": 100}, "within 1..10000"),
            ({"width": 100, "height": 100, "device_scale_factor": 9}, "0.1..5"),
            ("not an object", "must be an object"),
        ],
    )
    def test_bad_viewports_are_refused(self, raw, expected):
        with pytest.raises(ValueError, match=expected):
            _validated_viewport(raw)


class TestGeolocationValidation:
    def test_accuracy_defaults(self):
        assert _validated_geolocation({"latitude": 1.5, "longitude": -2.5}) == {
            "latitude": 1.5,
            "longitude": -2.5,
            "accuracy": 100.0,
        }

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"latitude": 1}, "needs latitude and longitude"),
            ({"latitude": 91, "longitude": 0}, "within -90..90"),
            ({"latitude": 0, "longitude": 181}, "within -180..180"),
            ({"latitude": 0, "longitude": 0, "accuracy": -1}, "must not be negative"),
        ],
    )
    def test_bad_coordinates_are_refused(self, raw, expected):
        with pytest.raises(ValueError, match=expected):
            _validated_geolocation(raw)


class TestPermissionValidation:
    def test_known_names_pass_through(self):
        assert _validated_permissions(["geolocation", "notifications"]) == [
            "geolocation",
            "notifications",
        ]

    def test_an_unknown_name_is_refused_with_suggestions(self):
        """CDP rejects the whole grant on one bad name, so a typo would leave
        every requested permission ungranted with no error."""

        with pytest.raises(ValueError, match="unknown browser permission"):
            _validated_permissions(["geolocation", "teleportation"])

        try:
            _validated_permissions(["camara"])
        except ValueError as exc:
            assert "geolocation" in str(exc)

    def test_blank_entries_are_dropped(self):
        assert _validated_permissions(["  ", "geolocation", ""]) == ["geolocation"]

    def test_absent_means_no_change(self):
        assert _validated_permissions(None) == []

    def test_the_name_list_matches_cdp(self):
        # Sourced from cdp_use's Browser.PermissionType; drifting silently would
        # start refusing names the browser accepts.
        from cdp_use.cdp.browser.types import PermissionType
        import typing

        assert set(typing.get_args(PermissionType)) == set(_CDP_PERMISSIONS)


def test_each_requested_change_is_forwarded(session):
    runtime, context, browser_id, calls = session

    _emulate(
        runtime,
        context,
        browser_id,
        viewport={"width": 390, "height": 844, "mobile": True},
        user_agent="LoomTest/1.0",
        geolocation={"latitude": 35.6762, "longitude": 139.6503},
        grant_permissions=["geolocation"],
    )

    assert calls[-1]["viewport"]["mobile"] is True
    assert calls[-1]["user_agent"] == "LoomTest/1.0"
    assert calls[-1]["geolocation"]["latitude"] == 35.6762
    assert calls[-1]["grant_permissions"] == ["geolocation"]
    assert calls[-1]["reset"] is False


def test_a_call_that_asks_for_nothing_is_refused(session):
    runtime, context, browser_id, _ = session

    with pytest.raises(ValueError, match="needs viewport, user_agent"):
        _emulate(runtime, context, browser_id)


def test_reset_alone_is_a_valid_request(session):
    runtime, context, browser_id, calls = session

    result = _emulate(runtime, context, browser_id, reset=True)

    assert calls[-1]["reset"] is True
    assert "reset" in result.data["applied"]


def test_an_oversized_user_agent_is_refused(session):
    runtime, context, browser_id, _ = session

    with pytest.raises(ValueError, match="exceeds 1,000 characters"):
        _emulate(runtime, context, browser_id, user_agent="u" * 1001)


def test_the_result_warns_that_a_loaded_page_may_not_re_read_it(session):
    runtime, context, browser_id, _ = session

    result = _emulate(runtime, context, browser_id, reset=True)

    assert "reloads" in result.content
    # reset also clears the viewport the backend set on connect, which is a
    # visible change rather than a return to the previous report.
    assert "real window size" in runtime.tools.get("browser_emulate").description

"""Letting the browser reach local addresses without opening credential endpoints.

allow_private_networks existed on both URL policies but nothing could set it, so
Loom's browser could never open the user's own dev server. Making it reachable
exposed a latent hole: the prohibited-host list was checked only when private
networks were forbidden, so one switch would have unblocked the cloud metadata
service along with localhost. The two are now separate questions.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserURLPolicy, BrowserURLPolicyError

# Both classes implement the same boundary and both had the same hole.
POLICIES = (BrowserSecurityPolicy, BrowserURLPolicy)

LOCAL = (
    "http://127.0.0.1:8765/",
    "http://localhost:8765/",
    "http://dev.localhost:3000/",
    "http://192.168.1.50/",
    "http://10.0.0.8:8080/",
)
NEVER_ALLOWED = (
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://metadata/computeMetadata/v1/",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://host.docker.internal:5000/",
    "http://gateway.docker.internal/",
    "http://kubernetes.default.svc/api/",
    "http://kubernetes.default/api/",
)


@pytest.mark.parametrize("policy_cls", POLICIES)
@pytest.mark.parametrize("url", LOCAL)
def test_local_addresses_are_blocked_by_default(policy_cls, url):
    policy = policy_cls(resolve_dns=False)
    with pytest.raises(BrowserURLPolicyError):
        policy.validate(url)


@pytest.mark.parametrize("policy_cls", POLICIES)
@pytest.mark.parametrize("url", LOCAL)
def test_local_addresses_open_when_the_switch_is_on(policy_cls, url):
    policy = policy_cls(allow_private_networks=True, resolve_dns=False)
    assert policy.validate(url) == url


@pytest.mark.parametrize("policy_cls", POLICIES)
@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("url", NEVER_ALLOWED)
def test_credential_endpoints_stay_blocked_either_way(policy_cls, private, url):
    """These hand out credentials; reaching a dev server is a different ask."""

    policy = policy_cls(allow_private_networks=private, resolve_dns=False)
    with pytest.raises(BrowserURLPolicyError):
        policy.validate(url)


@pytest.mark.parametrize("policy_cls", POLICIES)
@pytest.mark.parametrize("private", [False, True])
def test_the_public_web_is_unaffected(policy_cls, private):
    policy = policy_cls(allow_private_networks=private, resolve_dns=False)
    assert policy.validate("https://example.com/page") == "https://example.com/page"


@pytest.mark.parametrize("policy_cls", POLICIES)
def test_link_local_follows_the_switch_but_the_metadata_address_does_not(policy_cls):
    """169.254.0.0/16 is ordinary link-local; one address in it is not."""

    policy = policy_cls(allow_private_networks=True, resolve_dns=False)
    assert policy.validate("http://169.254.10.20/") == "http://169.254.10.20/"
    with pytest.raises(BrowserURLPolicyError, match="metadata"):
        policy.validate("http://169.254.169.254/")


def test_the_security_policy_default_list_still_covers_both_groups():
    from app.agent_runtime.browser_security import _DEFAULT_PROHIBITED

    assert "localhost" in _DEFAULT_PROHIBITED
    assert "*.localhost" in _DEFAULT_PROHIBITED
    assert "metadata.google.internal" in _DEFAULT_PROHIBITED
    assert "kubernetes.default.svc" in _DEFAULT_PROHIBITED


def test_the_runtime_switch_refuses_to_change_under_a_live_session(tmp_path):
    from app.agent_runtime.browser_runtime import BrowserRuntime
    from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
    from app.agent_runtime.storage import FileAgentSessionStore
    from app.agent_runtime.browser_session import BrowserPageState
    from app.agent_runtime.workspace_tools import loom_default_tools

    class _Noop:
        def execute_chat(self, _profile_id, _request):
            raise AssertionError("the model is never called here")

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None
        allow_private_networks = False

        def start(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def state(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def close(self):
            return None

    runtime = BrowserRuntime(
        platform=_Noop(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    try:
        assert runtime.browser_allow_private_networks is False
        # Idempotent, so a settings apply that changes nothing never has to refuse.
        assert runtime.browser_set_private_networks(False) == {
            "allow_private_networks": False,
            "changed": False,
        }

        runtime.browser_sessions.backend_factory = lambda options: _Backend()
        runtime.browser_sessions.start("owner-1")

        # Changing it mid-session would leave the model holding a browser under a
        # policy it was not opened with.
        with pytest.raises(RuntimeError, match="close the active browser session"):
            runtime.browser_set_private_networks(True)

        runtime.browser_sessions.close_all()
        assert runtime.browser_set_private_networks(True)["changed"] is True
        assert runtime.browser_allow_private_networks is True
    finally:
        runtime.close()


def test_an_address_without_a_scheme_is_a_missing_prefix_not_a_policy_refusal():
    """`dash.cloudflare.com` is how a destination is normally written.

    Rejecting it raised BrowserURLPolicyError, whose message is about navigation
    policy, so a missing prefix surfaced to the user as a security refusal.
    """

    from app.agent_runtime.browser_security import BrowserSecurityPolicy

    policy = BrowserSecurityPolicy(resolve_dns=False, allow_private_networks=True)

    assert policy.validate("dash.cloudflare.com") == "https://dash.cloudflare.com"
    assert policy.validate("www.example.com/a?b=c") == "https://www.example.com/a?b=c"
    # urlsplit reads `localhost` as the scheme here, so a colon cannot be the test.
    assert policy.validate("localhost:8080") == "https://localhost:8080"
    # An explicit scheme is still honoured verbatim.
    assert policy.validate("http://example.com") == "http://example.com"


def test_non_web_schemes_are_still_refused():
    """Prepending a scheme must not smuggle anything past the http/https check."""

    import pytest

    from app.agent_runtime.browser_security import BrowserSecurityPolicy
    from app.agent_runtime.browser_session import BrowserURLPolicyError

    policy = BrowserSecurityPolicy(resolve_dns=False, allow_private_networks=True)

    for value in (
        "javascript:alert(1)",
        "file:///C:/Windows/win.ini",
        "data:text/html,<script>x</script>",
        "about:blank",
        "chrome://settings",
        "view-source:https://example.com",
        "",
        "not a url at all",
    ):
        with pytest.raises(BrowserURLPolicyError):
            policy.validate(value)

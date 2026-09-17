"""Guards for the Chrome/Edge bridge extension, which has no JS test runner.

Both invariants here were found by driving a real Edge: a screenshot failed on a
missing permission, and a click on a link returned the page it had just left.
Neither is reachable from Python, so they are checked against the source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


EXTENSION = Path(__file__).resolve().parents[1] / "extensions" / "browser-current-tab"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def background() -> str:
    return (EXTENSION / "background.js").read_text(encoding="utf-8")


def test_manifest_requests_the_permission_screenshots_actually_need(manifest):
    """chrome.tabs.captureVisibleTab needs <all_urls> or a user-gesture activeTab.

    The extension is driven programmatically, so activeTab is never granted and
    http://*/* plus https://*/* is not enough: every browser_screenshot failed
    with "Either the '<all_urls>' or 'activeTab' permission is required."
    """

    origins = manifest.get("host_permissions") or []
    assert "<all_urls>" in origins
    assert "scripting" in (manifest.get("permissions") or [])
    assert "tabs" in (manifest.get("permissions") or [])


def test_manifest_stays_manifest_v3_with_a_service_worker(manifest):
    assert manifest["manifest_version"] == 3
    worker_name = manifest["background"]["service_worker"]
    assert worker_name in {"background.js", "bridge-worker.js"}
    worker = (EXTENSION / worker_name).read_text(encoding="utf-8")
    if worker_name != "background.js":
        # A wrapper may add extension-lifecycle repair, but the production bridge
        # runtime must still be the original background.js implementation.
        assert "import './background.js'" in worker
    assert "tabGroups" in manifest["permissions"]


def test_extension_has_no_public_development_credential(background):
    assert "loom-dev-browser-extension" not in background
    assert 'chrome.runtime.getURL("bridge-config.json")' in background
    options = (EXTENSION / "options.js").read_text(encoding="utf-8")
    assert "token" not in options


@pytest.mark.parametrize(
    "handler",
    ["withElement", "drag", "pressKey", "goBack"],
)
def test_actions_that_can_navigate_wait_for_the_navigation(background, handler):
    """A click that follows a link must not return the previous page.

    chrome.tabs.onUpdated only reports "loading" once the navigation commits,
    which on a slow origin is seconds after the click. Returning after a fixed
    sleep made the model read its own successful click as a no-op.
    """

    body = _function_body(background, handler)
    assert "withNavigationWatch" in body, (
        f"{handler} returns without waiting for a navigation it may have started"
    )


def test_scroll_stays_on_the_cheap_path(background):
    # Scrolling does not navigate, so it must not pay the navigation grace window.
    body = _function_body(background, "scroll")
    assert "withNavigationWatch" not in body
    assert "afterTabAction" in body


def test_navigation_reuses_an_existing_same_origin_tab(background):
    body = _function_body(background, "navigate")
    assert "new URL(url).origin" in body
    assert "chrome.tabs.query" in body
    assert "new URL(candidate.url).origin === targetOrigin" in body
    assert "args.new_tab" in body


def test_navigation_never_overwrites_an_unrelated_personal_tab(background):
    body = _function_body(background, "navigate")
    assert "isLoomWorkTab(existing)" in body
    assert "createWorkTab = true" in body
    assert "placeInLoomGroup" in body
    assert 'chrome.tabs.create({ url, active: true })' in body


def test_work_tabs_use_a_named_browser_group(background):
    assert 'LOOM_TAB_GROUP_TITLE = "Loom"' in background
    body = _function_body(background, "placeInLoomGroup")
    assert "chrome.tabs.group" in body
    assert "chrome.tabGroups.update" in body
    assert 'color: "purple"' in body


def test_installed_extension_can_reload_itself_after_desktop_update(background):
    assert 'chrome.runtime.getURL("extension-update.json")' in background
    assert "UPDATE_TOKEN_KEY" in background
    assert "chrome.runtime.reload()" in background
    assert "startInstalledUpdateWatcher()" in background


def test_click_reports_whether_a_navigation_is_coming(background):
    assert "navigationExpectedFor" in background
    click_body = _function_body(background, "clickElement")
    assert "navigation_expected" in click_body
    # _blank opens a new tab, so the watched tab never navigates and waiting for
    # it would stall every such click until the timeout.
    expected_body = _function_body(background, "navigationExpectedFor")
    assert "_blank" in expected_body
    assert "javascript:" in expected_body


def test_an_injected_failure_is_rethrown_instead_of_read_as_success(background):
    """chrome.scripting.executeScript resolves when the injected function throws.

    The thrown error is not in the result either, so every page action that
    failed came back as an ordinary state with no errors: a click on a stale
    index, or a select with no matching option, reported success. runPageAction
    returns the failure as data and inject rethrows it.
    """

    assert "__loomError" in _function_body(background, "runPageAction")
    inject = _function_body(background, "inject")
    assert "__loomError" in inject
    assert "throw new Error" in inject


def test_select_resolves_the_option_instead_of_assigning_select_value(background):
    """Assigning an unmatched string to select.value clears the selection.

    browser_select is called with the option text browser_dropdown_options
    reports, which is not the option value, so the old `el.value = wanted` left
    the select with nothing chosen and still reported success.
    """

    body = _function_body(background, "selectElement")
    assert "selectedIndex" in body
    assert "No option matches" in body
    # Matching has to accept either the option's value or its visible text.
    assert "option.value === wanted" in body
    assert "option.text" in body


def test_evaluating_a_string_runs_in_the_page_world(background):
    """The isolated world is governed by the extension's MV3 policy, which
    forbids evaluating a string, so eval there fails on every page."""

    assert '"MAIN"' in _function_body(background, "evaluate")
    assert '"MAIN"' in _function_body(background, "waitFor")


@pytest.mark.parametrize(
    "permission",
    ["cookies", "downloads"],
)
def test_manifest_requests_the_permissions_the_new_actions_use(manifest, permission):
    assert permission in (manifest.get("permissions") or [])


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    if match is None:
        raise AssertionError(f"{name} is missing from background.js")
    # Skip the parameter list before looking for the body: a default value like
    # `extra = {}` would otherwise be mistaken for the opening brace.
    depth = 1
    cursor = match.end()
    while depth:
        if source[cursor] == "(":
            depth += 1
        elif source[cursor] == ")":
            depth -= 1
        cursor += 1
    start = source.index("{", cursor)
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} has an unbalanced body")

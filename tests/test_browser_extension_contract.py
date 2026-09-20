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
    assert "alarms" in manifest["permissions"]


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


def test_navigation_reuses_a_tokenized_page_across_all_windows(background):
    body = _function_body(background, "resolveNavigationDestination")
    assert "new URL(url).href" in body
    assert "chrome.tabs.query({})" in body
    assert "new URL(candidate.url).href === targetUrl" in body
    assert "existing.origin === target.origin" in body
    assert "existing.pathname === target.pathname" in body
    assert "search.length" in body
    assert "target.search ? (exact || samePage) : (samePage || exact)" in body
    assert "preserveUrl: true" in body
    assert "args.new_tab" in body


def test_navigation_never_overwrites_an_unrelated_personal_tab(background):
    resolver = _function_body(background, "resolveNavigationDestination")
    navigate = _function_body(background, "navigate")
    assert "isLoomWorkTab(current)" in resolver
    assert "return { create: true, tab: current }" in resolver
    assert "placeInLoomGroup" in navigate


def test_loom_works_beside_the_user_instead_of_taking_the_foreground(background):
    """Every navigate fronted Loom's tab, pulling the user off their own page.

    Scripting, navigation and DOM capture all work on a background tab, so there
    is no reason to steal focus: Loom should open its own tab quietly and leave
    the user where they are, which is the whole point of the purple group.
    """

    navigate = _function_body(background, "navigate")
    assert "active: true" not in navigate, "new navigation steals the foreground from the user"
    assert "chrome.tabs.create({ url, active: false })" in navigate
    assert "chrome.tabs.update(destination.tab.id, { url })" in navigate

    # Screenshots are the one exception - captureVisibleTab only returns the
    # visible tab - so they front Loom's tab briefly and hand focus back.
    shot = _function_body(background, "screenshot")
    assert "captureVisibleTab" in shot
    assert "active: true" in shot
    assert "restore" in shot

    # An explicit switch is still an explicit switch.
    assert "active: true" in _function_body(background, "switchTab")
    # Reusing a page the user explicitly asked for fronts its existing window.
    reuse = _function_body(background, "focusExistingTab")
    assert "active: true" in reuse
    assert "chrome.windows.update" in reuse


def test_tab_listing_and_explicit_switch_work_across_browser_windows(background):
    listing = _function_body(background, "listOpenTabs")
    assert "chrome.tabs.query({})" in listing
    assert "window_id" in listing
    assert "current_window" in listing

    switch = _function_body(background, "switchTab")
    assert "chrome.tabs.get(tabId)" in switch
    assert "chrome.windows.update(target.windowId, { focused: true })" in switch


def test_work_tabs_use_a_named_browser_group(background):
    assert 'LOOM_TAB_GROUP_TITLE = "Loom"' in background
    body = _function_body(background, "placeInLoomGroup")
    assert "chrome.tabs.group" in body
    assert "chrome.tabGroups.update" in body
    assert 'color: "purple"' in body
    assert "OWNED_GROUP_IDS_KEY" in body
    assert "markLoomWorkTab" in body


def test_installed_extension_can_reload_itself_after_desktop_update(background):
    assert 'chrome.runtime.getURL("extension-update.json")' in background
    assert "UPDATE_TOKEN_KEY" in background
    assert "chrome.runtime.reload()" in background
    assert "startInstalledUpdateWatcher()" in background
    assert "chrome.alarms.create" in background
    assert "chrome.alarms.onAlarm.addListener" in background
    assert "setInterval(() => void checkForInstalledUpdate()" not in background


def test_extension_update_never_reloads_during_a_browser_command(background):
    check = _function_body(background, "applyInstalledUpdateAtCommandBoundary")
    loop = _function_body(background, "startPolling")
    alarm = _function_body(background, "requestInstalledUpdateCheck")
    assert "chrome.runtime.reload()" in check
    assert 'bridgeRuntime.phase = "idle"' in loop
    assert "await applyInstalledUpdateAtCommandBoundary()" in loop
    assert "bridgeRuntime.updateRequested = true" in alarm
    assert "applyInstalledUpdateAtCommandBoundary" not in alarm
    assert "commandInFlight" not in background
    assert "updateCheckRequested" not in background


def test_tab_ownership_is_explicit_and_session_scoped(background):
    store = _function_body(background, "ownedTabIds")
    ownership = _function_body(background, "isLoomWorkTab")
    resolver = _function_body(background, "resolveNavigationDestination")
    assert "chrome.storage.session" in store
    assert "OWNED_TAB_IDS_KEY" in store
    # Ownership is a recorded fact, never inferred from what the tab looks like:
    # matching on the group title would claim any tab the user put in a group
    # they happened to name the same thing.
    assert "group?.title" not in ownership
    assert "placeInLoomGroup(reusable" in resolver


def test_adopting_a_user_tab_makes_the_adoption_visible(background):
    """Reusing an already-open page silently made that tab Loom's.

    A navigate to a URL the user already has open reused their tab, and from then
    on it was a work tab: the next navigate to a different URL replaced what was
    on it. Nothing marked it, so the page simply disappeared. Adoption now goes
    through the group, which is the one thing the user can see.
    """

    resolver = _function_body(background, "resolveNavigationDestination")
    assert "markLoomWorkTab(reusable)" not in resolver, (
        "adopting a user tab without grouping it leaves the takeover invisible"
    )
    assert "placeInLoomGroup(reusable" in resolver


def test_only_borrowed_tabs_are_handed_back_when_the_session_closes(background):
    """Releasing Loom's own tabs too made it fight the user for the foreground.

    A tab borrowed from the user must be given back, or a later task navigates
    away a page they returned to. A tab Loom opened itself must not be: releasing
    those meant the next session did not recognise the tab it had just been
    working in, so it opened another one every time - observed in a real Edge as
    four tabs for one task, each stealing focus from what the user was reading.
    """

    release = _function_body(background, "releaseTabs")
    assert "ADOPTED_TAB_IDS_KEY" in release
    assert "OWNED_TAB_IDS_KEY" not in release, "releasing Loom's own tabs makes every session open another"
    # The group is the user's visible record of which tabs Loom touched, and the
    # next session reuses it rather than stacking up a second "Loom" group.
    assert "OWNED_GROUP_IDS_KEY" not in release
    assert '"release_tabs"' in _function_body(background, "dispatchCommand")

    # The two kinds have to be recorded apart for the above to mean anything.
    mark = _function_body(background, "markLoomWorkTab")
    assert "ADOPTED_TAB_IDS_KEY" in mark and "OWNED_TAB_IDS_KEY" in mark
    assert "adopted: true" in _function_body(background, "resolveNavigationDestination")


def test_navigation_reuses_loom_own_tab_rather_than_the_users_focus(background):
    """A session binds to whatever the user was looking at, which is not Loom's tab.

    Deciding create-vs-reuse from that tab alone meant every session concluded it
    had no work tab and opened a new one, pulling the foreground away each time.
    Loom's own tab in the window is the work tab wherever the user's attention is.
    """

    resolver = _function_body(background, "resolveNavigationDestination")
    assert "ownedTabIds()" in resolver
    assert "own.includes(candidate.id)" in resolver


def test_element_identity_survives_a_service_worker_restart(background):
    """MV3 evicts the worker between commands.

    The selector map lived in a module-level Map, so a restart emptied it while
    Loom's state_revision was still current: every index the model held failed
    with "refresh and retry" and the turn was wasted.
    """

    assert "lastElementsByTab" not in background
    assert "chrome.storage.session" in _function_body(background, "elementRefFor")
    assert "chrome.storage.session" in _function_body(background, "rememberElements")


def test_downloads_are_scoped_to_this_browser_session(background):
    """chrome.downloads.search sees the whole browser.

    Unfiltered, browser_downloads handed the model the filenames of the user's
    entire recent download history, which its own description says it does not do.
    """

    body = _function_body(background, "listDownloads")
    assert "since_ms" in body
    assert "startTime" in body
    # No session start time means nothing this session can honestly claim.
    assert "return { files: [] }" in body


def test_a_privileged_current_page_tells_the_agent_to_recover_automatically(background):
    collect = _function_body(background, "collectStateForTab")
    assert 'action: "browser_navigate"' in collect
    assert "creates_loom_work_tab: true" in collect
    assert "Do not ask the user to switch tabs" in collect


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


def test_a_password_field_reports_whether_it_is_filled_without_the_value(background):
    """A successful type into a password field looked exactly like a failed one.

    The value is withheld on purpose, but the serializer also emitted no value
    attribute at all, so the model could not tell "filled, hidden" from "empty".
    On a real Google Client Secret field it typed, read the field back as blank,
    retried, gave up on the browser tools and escalated to clicking the desktop -
    which made Loom capture the active window, and that window was not the browser.
    """

    collect = _function_body(background, "collectPageState")
    assert "const secret = type === \"password\"" in collect
    assert "value: clean(valued && !secret ? el.value : \"\")" in collect, (
        "a password value must never be serialized into model-visible state"
    )
    assert "filled: secret ? Boolean(valued && el.value) : false" in collect
    # A fact about the field, never a stand-in value: anything that reads like
    # text invites the model to type it back, which is how the retired transient
    # placeholder ended up being entered into a live form.
    assert 'filled="true" value-withheld="password"' in collect


def test_an_action_that_cannot_navigate_does_not_wait_for_one(background):
    """86% of element actions never navigate, and all of them paid the full grace.

    Measured against a real Edge: a click took 610ms of which ~500ms was sleeping,
    while capturing the resulting state took 46ms. The settle sleep already gives
    the page its chance to start a navigation, so when the page reports it starts
    none the second hedge is cut to a tick. Click went to ~344ms, hover to ~240ms.
    """

    assert "NAVIGATION_SETTLE_MS = 60" in background
    after = _function_body(background, "afterTabAction")
    assert "navigationRuledOut ? NAVIGATION_SETTLE_MS : NAVIGATION_GRACE_MS" in after
    # A page that says nothing either way keeps the original grace.
    assert "NAVIGATION_GRACE_MS" in after

    watch = _function_body(background, "withNavigationWatch")
    assert "outcome.navigation_expected === false" in watch
    assert "canNavigate" in watch


@pytest.mark.parametrize("handler", ["hover", "typeText"])
def test_only_actions_that_truly_cannot_navigate_are_marked(background, handler):
    # Typing dispatches input events and hovering dispatches mouse events; neither
    # follows a link.
    assert "canNavigate: false" in _function_body(background, handler)


@pytest.mark.parametrize("handler", ["click", "selectOption", "pressKey", "drag", "goBack"])
def test_actions_that_can_navigate_keep_the_full_grace(background, handler):
    """Enter submits, a jump menu navigates on change, a click follows a link.

    These must never be marked as unable to navigate: the short settle would let
    them return the page they just left, which is the bug the grace window exists
    to prevent.
    """

    assert "canNavigate: false" not in _function_body(background, handler)


def test_the_bridge_is_not_re_registered_before_every_poll(background):
    """A register POST between every command and the next was pure round trip.

    The bridge wants the active tab only for its status display, and the poll is
    what proves the extension is alive. One smoke run went from 32 registrations
    to 1.
    """

    loop = _function_body(background, "startPolling")
    assert "REGISTER_INTERVAL_MS" in loop
    assert "bridgeRuntime.registeredAt" in loop
    assert "TAB_COMPLETE_POLL_MS" in _function_body(background, "waitForTabComplete")


def test_the_hud_is_driven_by_session_state_not_by_the_current_action(background):
    """The HUD vanished on every page load and returned on the next click.

    Its visibility came from the per-action host the page action creates and then
    deletes on a timer, and a page load destroys everything in the page, so
    between actions - and after any reload the user performed themselves - there
    was nothing left to show. Session state lives in the extension instead, so a
    freshly injected content script can restore the HUD with no action at all.
    """

    hud = (EXTENSION / "browser-hud.js").read_text(encoding="utf-8")
    assert "SESSION_ACTIVE_KEY" in background
    assert "chrome.storage.session.setAccessLevel" in background, (
        "content scripts cannot read storage.session without this"
    )

    # Set on any command, cleared when the browser is handed back.
    dispatch = _function_body(background, "dispatchCommand")
    assert 'if (action !== "release_tabs") await markSessionActive(true)' in dispatch
    assert "markSessionActive(false)" in _function_body(background, "releaseTabs")
    # Only on a transition, or every command wakes the listener in every page.
    mark = _function_body(background, "markSessionActive")
    assert "=== next) return" in mark

    # The content script restores itself and follows later changes.
    assert "void refreshSession()" in _function_body(hud, "begin")
    assert "chrome.storage.session.get(SESSION_ACTIVE_KEY)" in _function_body(hud, "refreshSession")
    assert "chrome.storage.onChanged.addListener" in _function_body(hud, "watchSession")
    apply_body = _function_body(hud, "applySession")
    assert "classList.add('live')" in apply_body
    assert "classList.remove('live')" in apply_body, "nothing hides the HUD when the session ends"


def test_the_hud_only_shows_in_the_tabs_loom_is_driving(background):
    """The HUD appeared in every tab, including ones the user opened themselves.

    browser-hud.js is a content script, so it runs in every web page, and its
    visibility came from a single session-wide boolean with no tab in it: the
    moment Loom touched the browser, every open tab and every tab opened after
    that was framed in the full-screen HUD. The worker now publishes the tabs a
    command actually resolved to, and each page shows the HUD only for its own.
    """

    hud = (EXTENSION / "browser-hud.js").read_text(encoding="utf-8")

    assert "HUD_TAB_IDS_KEY" in background
    # Marking is deliberately not inside tabFromArgs: navigate resolves the
    # current tab before deciding to open a different one, so marking there put
    # the HUD in the user's own page every time Loom opened a tab beside it.
    assert "markHudTab" not in _function_body(background, "tabFromArgs")
    assert "markHudTab" in _function_body(background, "actionTab")
    assert "actionTab" in _function_body(background, "withElement")
    assert "actionTab" in _function_body(background, "requireInjectableTab")
    assert "markHudTab" in _function_body(background, "navigate")
    assert "HUD_TAB_IDS_KEY" in _function_body(background, "releaseTabs")
    # A closed tab must not leave its id behind for whatever reuses the number.
    assert "forgetHudTab" in background

    # A content script has no API for its own tab id, so the sender supplies it.
    assert "chrome.runtime.onMessage.addListener" in background
    assert "sender?.tab?.id" in background
    assert "ensureTabId" in _function_body(hud, "refreshSession")
    assert "HUD_TAB_IDS_KEY" in _function_body(hud, "refreshSession")
    assert "HUD_TAB_IDS_KEY" in _function_body(hud, "watchSession")
    # Without an identity a page cannot claim to be a work tab.
    driven = _function_body(hud, "driven")
    assert "sessionActive" in driven
    assert "tabId !== null" in driven
    assert "drivenTabs.includes(tabId)" in driven
    # An action's own markup must not be a second way to become visible.
    assert "if (driven()) view.hud.classList.add('live')" in _function_body(hud, "renderFromSource")


def test_opening_a_tab_beside_the_user_leaves_their_page_untouched(background):
    """A navigate that creates a tab still painted a status pill on the user's.

    destination.tab is whatever they were reading until the new tab exists, so
    the "Opening page" pill was injected into their page and then Loom worked
    somewhere else entirely.
    """

    body = _function_body(background, "navigate")
    assert "!destination.create && isInjectableUrl" in body


def test_clickable_cards_without_a_role_still_get_an_index(background):
    """A div with a JS handler is clickable on screen and was invisible here.

    The capture only looked for elements a browser already treats as interactive,
    so an app card built from a plain div - no role, no tabindex, and no el.onclick
    because the handler came from addEventListener - had no index. The model could
    see one in a screenshot, had nothing to click, and fell back to a screen
    coordinate: it missed and hit a sidebar link, which navigated the page away.
    cursor:pointer is how the page itself tells a person the thing is clickable.
    """

    body = _function_body(background, "collectClickableElements")
    assert 'style.cursor !== "pointer"' in body
    assert "MAX_POINTER_SCAN" in body, "an unbounded scan would cost the whole document"
    assert "MAX_ELEMENTS" in body
    # Outermost only. A card, its title and its icon all inherit the pointer
    # cursor, and three indexes for one target is worse than none.
    assert "other.contains(el) || el.contains(other)" in body
    # Indexes have to read in document order or they stop matching the page.
    assert "compareDocumentPosition" in body

    # The style is computed once per element, not once for visibility and again
    # for the cursor.
    assert "visibleStyle" in _function_body(background, "visible")

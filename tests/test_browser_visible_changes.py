"""The browser observation says what visibly changed since the previous one."""

from types import SimpleNamespace

from app.agent_runtime.browser_runtime_v1 import _observation_text, _visible_changes


def state(dom):
    return SimpleNamespace(dom=dom)


BEFORE = "[12]<button />\n\tSelect\nItem 72\nItem 73\n[19]<button id=save />\n\tSave settings\n"


def test_new_text_is_reported_and_renumbering_is_not():
    after = BEFORE.replace("[12]", "[40]").replace("[19]", "[41]") + "Selected Item 73\n"
    changes = _visible_changes(state(BEFORE), state(after))
    assert changes == '+ ["Selected Item 73"]'


def test_removed_text_and_overflow_are_summarized():
    before = BEFORE + "Loading...\n"
    after = BEFORE + "".join(f"Row {i}\n" for i in range(12))
    changes = _visible_changes(state(before), state(after))
    assert '"Row 0"' in changes and "(+4 more)" in changes and '- ["Loading..."]' in changes


def test_an_unchanged_page_says_nothing():
    # The state covers the page in view; a change below the fold is not "no change".
    assert _visible_changes(state(BEFORE), state(BEFORE)) == ""


def test_the_change_line_rides_in_the_transient_observation():
    snapshot = SimpleNamespace(browser_id="b", state_revision=3, state=SimpleNamespace(
        dom="x", url="http://127.0.0.1/", title="t", tabs=(), errors=(), page_info=None))
    text = _observation_text(snapshot, effect="changed", effect_reason="dom", changes='+ ["Saved"]')
    assert 'text_changes_since_previous_observation: + ["Saved"]' in text
    assert "text_changes" not in _observation_text(snapshot, effect="changed", effect_reason="dom")


def test_browser_use_prefixes_are_not_page_text():
    before = "|SHADOW(open)|[2]<input id=u />\nSign in\n"
    after = "|SHADOW(open)|[9]<input id=u />\n*[11]<div />\nSign in\n"
    assert _visible_changes(state(before), state(after)) == ""


def test_extension_positions_moving_on_scroll_are_not_changes():
    before = '[0] <button> text="Select" rect=10,40,80x24\n'
    after = '[0] <button> text="Select" rect=10,-400,80x24\n[1] <div> text="Selected Item 73" rect=10,20,200x20\n'
    assert _visible_changes(state(before), state(after)) == '+ ["<div> text=\\"Selected Item 73\\""]'


def test_a_ticked_checkbox_is_a_visible_change():
    before = '[0] <input> type="checkbox" label="Email notifications" checked="false" rect=24,96,13x13\n'
    after = '[0] <input> type="checkbox" label="Email notifications" checked="true" rect=24,96,13x13\n'
    changes = _visible_changes(state(before), state(after))
    assert 'checked=\\"true\\"' in changes and 'checked=\\"false\\"' in changes

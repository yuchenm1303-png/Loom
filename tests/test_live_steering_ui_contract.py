from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SRC = ROOT / "desktop-react" / "src"
COMPOSER = DESKTOP_SRC / "components" / "Composer.tsx"
TRANSCRIPT = DESKTOP_SRC / "components" / "Transcript.tsx"
TURN_FLOW = DESKTOP_SRC / "components" / "turn-flow.css"
LOOM_STATE = DESKTOP_SRC / "state" / "useLoom.ts"


def test_legacy_running_composer_css_never_hides_an_input_row() -> None:
    """The retired running-footer contract must not silently return."""

    selector = re.compile(
        r"\.composer\.is-running\s+\.composer-input-row\s*\{(?P<body>[^}]*)\}",
        re.MULTILINE,
    )
    hidden = re.compile(r"display\s*:\s*none\b", re.IGNORECASE)

    offenders: list[str] = []
    for path in DESKTOP_SRC.rglob("*.css"):
        source = path.read_text(encoding="utf-8")
        for match in selector.finditer(source):
            if hidden.search(match.group("body")):
                offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        "Live steering requires an editable input while a turn is active; "
        f"remove the stale running-state suppression from: {offenders}"
    )


def test_running_turn_uses_a_dedicated_editable_steering_state() -> None:
    source = COMPOSER.read_text(encoding="utf-8")

    assert 'if (props.running) return <SteeringComposer {...props} />;' in source
    assert 'className={`composer is-steering ${focused ? "is-focused" : ""}`}' in source
    assert 'className={`composer is-running ${focused ? "is-focused" : ""}`}' not in source
    assert '"Guide the current task…"' in source
    assert 'aria-label="Guide the current task"' in source

    # Ordinary running work is editable. Only an in-flight steer request or an
    # explicitly requested stop may disable this textarea.
    assert 'disabled={sending || stopping}' in source
    assert 'disabled={disabled || sending || stopping}' not in source
    assert 'type="button"\n              className="send-button stop"' in source


def test_running_send_is_routed_to_turn_steer_not_a_second_turn_start() -> None:
    source = LOOM_STATE.read_text(encoding="utf-8")

    assert 'const running = Boolean(loom.turnActive || threadIsRunning(thread));' in source
    assert 'if (!running) {' in source
    assert 'await loom.send(input, attachments);' in source
    assert 'await window.loom.call("turn/steer", {' in source
    assert 'turnId: activeTurn.currentTurnId' in source



def test_steering_bubbles_are_not_hoisted_next_to_the_initial_prompt() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")

    assert 'String(item.source ?? "").trim().toLowerCase() === "steering"' in source
    assert "function placeSteeringAtSubmissionTime(items: TranscriptItem[]): TranscriptItem[]" in source
    assert "const orderedItems = placeSteeringAtSubmissionTime(items);" in source
    assert 'const initialUser = userItems.find((item) => !isSteeringUserMessage(item)) ?? userItems[0] ?? null;' in source
    assert "const guidanceItems = userItems.filter((item) => item.id !== initialUser?.id);" in source
    assert "item.id !== initialUser?.id" in source
    assert '|| item.type === "user_message"' in source

    # Regression guard for the old behavior that rendered every same-turn user
    # message above the execution process.
    assert "derived.userItems.map" not in source
    assert 'isSteeringUserMessage(block.item) ? "entry-steering-user" : ""' in source


def test_steering_uses_submission_time_and_remains_visible_when_process_folds() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")
    styles = TURN_FLOW.read_text(encoding="utf-8")

    assert "const value = item.submittedAt ?? item.createdAt;" in source
    assert "candidateAt > submittedAt" in source
    assert 'guidanceItems={derived.guidanceItems}' in source
    assert '!active && !open && guidanceItems.length' in source
    assert 'className="turn-guidance-recap"' in source

    assert ".turn-process.has-guidance" in styles
    assert ".turn-process-content .entry-steering-user" in styles
    assert ".turn-guidance-recap" in styles

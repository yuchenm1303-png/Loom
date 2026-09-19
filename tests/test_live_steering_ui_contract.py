from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SRC = ROOT / "desktop-react" / "src"
COMPOSER = DESKTOP_SRC / "components" / "Composer.tsx"
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

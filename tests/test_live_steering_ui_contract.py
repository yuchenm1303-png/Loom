from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SRC = ROOT / "desktop-react" / "src"
COMPOSER = DESKTOP_SRC / "components" / "Composer.tsx"


def test_running_composer_css_never_hides_the_steering_input_row() -> None:
    """A running turn owns an editable steering composer, not a waiting footer."""

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
        "Live steering requires the running composer input row to remain visible; "
        f"remove the stale running-state suppression from: {offenders}"
    )


def test_running_composer_renders_an_editable_steering_textarea() -> None:
    source = COMPOSER.read_text(encoding="utf-8")

    assert 'if (props.running) return <SteeringComposer {...props} />;' in source
    assert 'placeholder="Guide the current task…"' in source
    assert 'aria-label="Guide the current task"' in source
    assert 'disabled={sending || stopping}' in source
    assert 'disabled={disabled || sending || stopping}' not in source

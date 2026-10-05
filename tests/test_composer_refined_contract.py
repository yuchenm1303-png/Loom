"""Contracts for the composer's refinement layer (composer-refined.css).

The composer and the popovers that open from it had been patched by a dozen
sheets: gradients, inner highlights, coloured glows, lift-on-hover, a light
streak across every chip. Together they read as moulded plastic. The refinement
layer replaces all of that with hairlines, one surface colour and tints for
state. These tests keep it that way: no gloss, no motion beyond a colour fade,
colour only where it means something, and everything scoped to the composer.

The model card (the popover behind the model chip: model identity, the reasoning
thread, the model library) is the exception. It was given back to its own sheets
at the user's request, so the layer must stay out of it.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
REFINED = COMPONENTS / "composer-refined.css"

SCOPE = ":root .composer-wrap.composer-refined"
LIGHT_SCOPE = ':root[data-loom-theme="light"] .composer-wrap.composer-refined'


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def stripped(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def rules(css: str) -> list[tuple[str, str]]:
    """Innermost `selector { body }` pairs, comments stripped."""
    return [(selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", stripped(css))]


def body_of(css: str, selector: str) -> str:
    for candidate, body in rules(css):
        if selector in [part.strip() for part in candidate.split(",")]:
            return body
    raise AssertionError(f"no rule for {selector}")


def tokens(block: str) -> set[str]:
    return set(re.findall(r"(--cq-[\w-]+)\s*:", block))


def test_refinement_loads_after_base_composer_sheets_before_workspace_geometry() -> None:
    main = read(SRC / "main.tsx")
    imports = re.findall(r'import "(\./[^"]+\.css)";', main)

    # 870e93c2 introduced a later workspace layer for shared surface geometry.
    assert imports[-2:] == ["./components/composer-refined.css", "./components/workspace-surface-refinement.css"]
    workspace = read(COMPONENTS / "workspace-surface-refinement.css")
    assert ":root .app-shell .composer-wrap.composer-refined .composer { border-radius: 14px; }" in workspace
    for earlier in (
        "./theme.css",
        "./components/permission-popover-polish.css",
        "./components/model-picker.css",
        "./components/composer-control-pills.css",
        "./components/conversation-home.css",
    ):
        assert imports.index(earlier) < imports.index("./components/composer-refined.css"), earlier


def test_both_composer_surfaces_opt_in() -> None:
    base = read(COMPONENTS / "ComposerBase.tsx")
    steering = read(COMPONENTS / "Composer.tsx")

    assert '<div className="composer-wrap composer-refined" ref={composerRootRef}>' in base
    assert '<div className="composer-wrap composer-refined live-steering-composer">' in steering
    # The permission chip carries its mode, so unrestricted access can look different.
    assert 'data-mode={permissionMode || "approval"}' in base
    # Other contracts rely on these elements existing; the layer hides them in CSS.
    for element in ('className="composer-glow" aria-hidden="true"', 'className="composer-spark" aria-hidden="true"'):
        assert element in base and element in steering


def test_every_rule_is_scoped_to_the_composer() -> None:
    css = read(REFINED)
    found = rules(css)

    assert len(found) > 100
    for selector_list, _ in found:
        for selector in selector_list.split(","):
            assert selector.strip().startswith((SCOPE, LIGHT_SCOPE)), selector.strip()


def test_both_themes_define_the_same_tokens() -> None:
    css = read(REFINED)
    dark = body_of(css, SCOPE)
    light = body_of(css, LIGHT_SCOPE)

    assert tokens(dark) and tokens(light) <= tokens(dark)
    # Light only restates what actually differs; the shared tokens stay in one place.
    for shared in ("--cq-accent", "--cq-warn", "--cq-safe", "--cq-danger"):
        assert shared in tokens(dark)
    for essential in ("--cq-surface", "--cq-pop", "--cq-line", "--cq-hover", "--cq-selected", "--cq-ink", "--cq-ink-4", "--cq-send", "--cq-shadow-pop"):
        assert essential in tokens(light), essential


def test_nothing_glossy_survives() -> None:
    css = stripped(read(REFINED))

    for gloss in ("radial-gradient", "conic-gradient", "filter: blur", "backdrop-filter", "inset 0 1px", "will-change"):
        assert gloss not in css, gloss
    # The one linear gradient is the slider's track (fill / remainder).
    assert css.count("linear-gradient(") == 1
    assert "sticker-setting input[type=\"range\"] {" in css.split("linear-gradient(")[0].rsplit("\n\n", 1)[-1]
    # Accent colour comes from the theme variable, never a literal.
    assert "139, 124, 246" not in css and "#8b7cf6" not in css.lower()


def test_nothing_lifts_scales_or_sweeps() -> None:
    css = stripped(read(REFINED))

    assert "@keyframes" not in css and "animation" not in css
    transforms = re.findall(r"(?<![\w-])transform\s*:\s*([^;]+);", css)
    assert transforms and set(transforms) <= {"none", "rotate(180deg)"}
    # No standalone transform properties either (`scale:`, `translate:`, `rotate:`).
    assert not re.findall(r"(?<![\w-])(?:scale|translate|rotate)\s*:", css)


def test_motion_is_a_colour_fade_only() -> None:
    css = stripped(read(REFINED))

    declarations = re.findall(r"transition\s*:\s*([^;]+);", css)
    assert declarations
    for declaration in declarations:
        properties = {part.strip().split()[0] for part in declaration.split(",")}
        assert properties <= {"background-color", "color", "opacity"}, declaration


def test_controls_are_plain_until_touched() -> None:
    css = read(REFINED)

    resting = body_of(css, f"{SCOPE} .composer .composer-tool")
    assert "border: 0;" in resting and "background: transparent;" in resting and "box-shadow: none;" in resting
    for state, token in (
        (f"{SCOPE} .composer .composer-chip:hover:not(:disabled)", "var(--cq-hover)"),
        (f"{SCOPE} .composer .composer-chip.is-open", "var(--cq-selected)"),
        (f"{SCOPE} .composer-option:hover:not(:disabled)", "var(--cq-hover)"),
        (f"{SCOPE} .composer-option.active", "var(--cq-selected)"),
    ):
        assert f"background: {token};" in body_of(css, state), state
    # Hover and open never lift or scale.
    assert "transform: none;" in body_of(css, f"{SCOPE} .composer .composer-chip.is-open")


def test_decorations_the_old_sheets_added_are_switched_off() -> None:
    css = read(REFINED)

    hidden = {
        selector.strip()
        for selector_list, body in rules(css)
        if "display: none;" in body
        for selector in selector_list.split(",")
    }
    for decoration in (
        f"{SCOPE}::before",
        f"{SCOPE} .composer::before",
        f"{SCOPE} .composer::after",
        f"{SCOPE} .composer-glow",
        f"{SCOPE} .composer-spark",
        f"{SCOPE} .composer .composer-chip::before",
        f"{SCOPE} .composer .send-button::after",
        f"{SCOPE} .composer-popover:not(.model-manager-popover)::before",
        f"{SCOPE} .composer-popover-icon",
        f"{SCOPE} .sticker-panel-symbol",
    ):
        assert decoration in hidden, decoration


def test_colour_on_the_toolbar_means_something() -> None:
    css = read(REFINED)

    # Every icon is one neutral ink...
    icons = body_of(css, f"{SCOPE} .composer .composer-tool > svg")
    assert "color: var(--cq-ink-3);" in icons
    # ...except the shield when access is unrestricted.
    warning = body_of(css, f'{SCOPE} .composer .permission-chip[data-mode="full-access"] > svg:not(.composer-chip-chevron)')
    assert "color: var(--cq-warn);" in warning
    # Send is the one primary action: flat and inverse, in both themes.
    send = body_of(css, f"{SCOPE} .composer .send-button:not(:disabled):not(.stop)")
    assert "background: var(--cq-send);" in send and "color: var(--cq-send-ink);" in send


def test_permission_tags_are_tinted_words_not_outlined_pills() -> None:
    css = read(REFINED)

    badge = body_of(css, f"{SCOPE} .permission-badge")
    assert "border: 0;" in badge and "border-radius: 5px;" in badge
    for tone, token in (("safe", "--cq-safe"), ("danger", "--cq-danger")):
        body = body_of(css, f"{SCOPE} .permission-badge.{tone}")
        assert f"var({token})" in body
    # The selected row is a tint with a check, not a bordered card with a bar.
    active = body_of(css, f"{SCOPE} .composer-option.active")
    assert "border: 0;" in active and "box-shadow: none;" in active


def test_the_model_card_keeps_its_own_look() -> None:
    """The model card went back to model-core-redesign.css and model-picker.css."""
    css = read(REFINED)
    guard = ":not(.model-manager-popover)"
    raw = [selector.strip() for selector_list, _ in rules(css) for selector in selector_list.split(",")]

    # Nothing in the layer styles the card: not its identity, the reasoning thread
    # (.rt), the model library (.mp) or the popover that holds them. The only
    # mention allowed is the guard that keeps the shared popover frame off it.
    card = re.compile(r"\.(?:mp|rt)(?!\w)|\.(?:mp|rt)-|model-manager|model-core|model-popover")
    for selector in raw:
        assert not card.search(selector.replace(guard, "")), selector
    # Its token sets are not redirected either.
    body = stripped(css)
    assert "--mp-" not in body and "--rt-" not in body and "--cq-thread" not in body

    # The shared popover frame is the one rule that would otherwise reach it.
    frame = f"{SCOPE} .composer-popover"
    assert f"{frame}{guard}" in raw and f"{frame}{guard}::before" in raw
    for selector in raw:
        assert selector not in (frame, f"{frame}::before"), selector

    # The layer still opts in only through the composer wrap, so the card's own
    # sheets keep winning inside it.
    for selector in raw:
        assert selector.startswith((SCOPE, LIGHT_SCOPE)), selector

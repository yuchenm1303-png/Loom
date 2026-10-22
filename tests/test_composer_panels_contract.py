"""The composer's panels are one family.

Tools & skills, permissions, the model card (with its library, a provider's models and two forms), balance & quota and
the draft preview used to be five designs on ten stacked sheets: five headers, three corners, a blue bead with a comet
and a burst inside a violet product, three panels with no entrance or exit at all, native purple checkboxes, text at 8
and 10px. Now one kit (composerPanel.tsx) gives them one shell, one host (which panel is open, how it closes, where the
focus goes) and one anatomy, and one sheet (composer-panels.css) is the only one that styles them.
docs/composer-panels-2026-10-22.md says why. These tests read the sources as text, like the other source contracts.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
SHEET = COMPONENTS / "composer-panels.css"
KIT = COMPONENTS / "composerPanel.tsx"

PANELS = {
    "ComposerBase.tsx": 2,  # permissions, model card
    "ComposerToolSelector.tsx": 1,
    "ModelUsageIndicator.tsx": 1,
    "ComposerDraftTools.tsx": 1,
}
RETIRED_SHEETS = (
    "model-picker.css", "model-core-redesign.css", "reasoning-thread.css", "permission-popover-polish.css",
    "model-panel-overrides.css", "model-panel.css", "composer-tool-selector.css", "model-usage.css",
)


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


def bodies_of(css: str, selector: str) -> str:
    """Every declaration the rules naming exactly this selector carry, in source order."""
    found = [body for candidate, body in rules(css) if selector in [part.strip() for part in candidate.split(",")]]
    assert found, f"no rule for {selector}"
    return "".join(found)


def stylesheets() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.css") if "node_modules" not in path.parts)


def test_every_panel_wears_the_one_shell() -> None:
    for name, count in PANELS.items():
        source = read(COMPONENTS / name)
        assert source.count("<ComposerPanel panel=") == count, name
        # No panel draws its own frame any more.
        assert 'className="composer-popover' not in source and "className={`composer-popover" not in source, name
    kit = read(KIT)
    # The hook classes the older selectors and tests find a panel by ride on the shell; they carry no style.
    assert '["cp", ...className.split(/\\s+/), "composer-popover"]' in kit
    assert "data-composer-panel={panel.id}" in kit


def test_one_host_opens_and_closes_them() -> None:
    kit = read(KIT)
    # One open at a time; a press outside the open panel, or Escape, closes it, and Escape gives the focus back.
    assert 'document.addEventListener("pointerdown", onPointerDown)' in kit
    assert 'document.addEventListener("keydown", onKeyDown)' in kit
    assert "triggerRef.current?.current?.focus({ preventScroll: true })" in kit
    for name in PANELS:
        source = read(COMPONENTS / name)
        assert 'addEventListener("pointerdown"' not in source, name
        assert 'addEventListener("keydown"' not in source, name
    # Both composers lend their host to everything inside them, so the live-guidance preview and the shared controls
    # close each other too, and the composer knows a panel is up for as long as one is on screen.
    base = read(COMPONENTS / "ComposerBase.tsx")
    steering = read(COMPONENTS / "Composer.tsx")
    assert "<ComposerPanelHost host={panels}>" in base and "<ComposerPanelHost host={panels}>" in steering
    assert '${panels.mounted ? "has-panel" : ""}' in base and '${panels.mounted ? "has-panel" : ""}' in steering
    assert "useInheritedComposerPanelHost()" in base


def test_one_sheet_styles_them() -> None:
    family = re.compile(r"\.cp(?![\w-])|\.cp-[\w-]+")
    others = []
    for path in stylesheets():
        if path == SHEET:
            continue
        for selector, _ in rules(read(path)):
            if family.search(selector):
                others.append(f"{path.name}: {selector[:80]}")
    assert not others, others
    for name in RETIRED_SHEETS:
        assert not (COMPONENTS / name).exists(), name
    assert not (COMPONENTS / "ReasoningThread.tsx").exists()
    # What the retired sheets styled is gone everywhere, so nothing can creep back over the family.
    retired = re.compile(r"\.(?:mp|rt)(?:-[\w-]+)?(?![\w-])|\.model-core-|\.composer-option|\.permission-badge|\.composer-popover-")
    for path in stylesheets():
        for selector, _ in rules(read(path)):
            assert not retired.search(selector), f"{path.name}: {selector[:80]}"


def test_the_shell_is_the_float_and_nothing_glossy() -> None:
    css = read(SHEET)
    tokens = body_of(css, ".cp")
    assert "--cp-shadow: var(--pl-shadow-float" in css and "--cp-radius: var(--pl-radius-float" in css
    shell = [body for selector, body in rules(css) if selector == ".cp" and "position: absolute" in body][0]
    for declaration in ("background: var(--cp-surface);", "border: 1px solid var(--cp-line);",
                        "border-radius: var(--cp-radius);", "box-shadow: var(--cp-shadow);"):
        assert declaration in shell, declaration
    assert "--cp-surface: var(--loom-workspace-panel" in tokens
    body = stripped(css)
    for gloss in ("radial-gradient", "conic-gradient", "linear-gradient", "backdrop-filter", "filter: blur", "inset 0 1px", ":has("):
        assert gloss not in body, gloss
    # The light theme restates only what differs, under the same names.
    light = body_of(css, 'html[data-loom-theme="light"] .cp')
    dark_names = set(re.findall(r"(--cp-[\w-]+)\s*:", tokens))
    light_names = set(re.findall(r"(--cp-[\w-]+)\s*:", light))
    assert light_names and light_names <= dark_names


def test_they_come_and_go_as_one_popover() -> None:
    css = read(SHEET)
    shell = [body for selector, body in rules(css) if selector == ".cp" and "position: absolute" in body][0]
    assert "transition: opacity var(--loom-dur-fast) var(--loom-ease-out);" in shell
    assert "transform: none;" in shell
    exiting = body_of(css, '.cp[data-motion-phase="exiting"]')
    for declaration in ("pointer-events: none;", "opacity: 0;", "transition-duration: var(--loom-dur-fast-out);",
                        "transition-timing-function: var(--loom-ease-exit);"):
        assert declaration in exiting, declaration
    # Readable on the first frame, and the @starting-style comes after the rule it starts from.
    starts = css.index('@starting-style {\n  .cp[data-motion-phase="entered"] {\n    opacity: .72;')
    assert starts > css.index('.cp[data-motion-phase="exiting"] {')
    # A view or a tab swapped in starts where a panel starts.
    assert "@starting-style {\n  .cp-view {\n    opacity: .72;" in css
    # Words never move: nothing that carries text in the family has a transform or travels.
    for selector, body in rules(css):
        if re.search(r"\.cp(-view|-head|-title|-row|-caption|-label|-foot|-note)?(?![\w-])", selector) and "::" not in selector:
            assert not re.search(r"(?<![\w-])(translate|scale|rotate)\s*:", body), selector
            transform = re.search(r"(?<![\w-])transform\s*:\s*([^;]+);", body)
            assert transform is None or transform.group(1) == "none", selector


def test_only_marks_travel() -> None:
    css = read(SHEET)
    # The reasoning needle with its pin, and the window that reveals the lit ticks as it passes, and the segmented slab:
    # the settle spring on the flow step.
    for selector in (".cp-lit", ".cp-lit-ticks", ".cp-slide", ".cp-needle", ".cp-pin"):
        assert "transition: transform var(--loom-dur-flow) var(--loom-spring-settle);" in bodies_of(css, selector), selector
    slab = body_of(css, ".cp-seg[data-ready] .cp-seg-slab")
    assert "transform var(--loom-dur-flow) var(--loom-spring-settle)" in slab
    # A quota bar fills on the slow step.
    assert "transition: width var(--loom-dur-slow) var(--loom-ease-move);" in body_of(css, ".cp-meter > i")


def test_colour_has_one_meaning() -> None:
    css = read(SHEET)
    accent = sorted({
        part.strip()
        for selector, body in rules(css)
        if re.search(r"var\(--cp-accent(-ink)?[,)]", body) and selector != ".cp"
        for part in selector.split(",")
    })
    # The accent marks the current choice: a check, a ticked box, the lit ticks of the reasoning scale (the stretch up to
    # the chosen level), the word "Recommended", the count of a custom tool set. Nothing else is violet.
    assert accent == sorted([
        ".composer-toolbar .composer-tools-count",
        ".cp-check:checked",
        ".cp-check:indeterminate",
        ".cp-lit .cp-ticks > .is-major",
        ".cp-lit .cp-ticks > .is-peek",
        ".cp-lit .cp-ticks > span",
        ".cp-row-end.is-check",
        ".cp-tag.is-accent",
    ]), accent
    # Unrestricted access and a low quota are the warn colour; tags are words, not pills.
    assert "color: var(--cp-warn);" in body_of(css, ".cp-row-lead.is-warn")
    assert "color: var(--cp-warn);" in body_of(css, ".cp-tag.is-warn")
    tag = body_of(css, ".cp-tag")
    assert "background" not in tag and "border" not in tag and "padding" not in tag


def test_the_reasoning_scale_is_quiet() -> None:
    source = read(COMPONENTS / "ReasoningControl.tsx")
    css = read(SHEET)
    # Ticks, a lit copy of them and one needle: no comet, aura, burst, orbit or ghost.
    for gone in ("comet", "aura", "burst", "orbit", "ghost", "strand", "spine"):
        assert gone not in source and gone not in stripped(css), gone
    for part in ('className="cp-scale"', 'className="cp-lit"', 'className="cp-slide"', 'className="cp-needle"',
                 'className="cp-pin"', 'className="cp-ticks"'):
        assert part in source, part
    # The scale owns its arrows (the panel's row walk leaves them alone) and a level is never disabled mid-change.
    assert "event.preventDefault();" in source and "aria-disabled={locked || count < 2 || undefined}" in source
    # The window that reveals the lit copy of the ticks clips it (a few px past the scale: the first and the last tick are
    # centred on its ends).
    window = bodies_of(css, ".cp-lit")
    assert "overflow: clip;" in window and "overflow-clip-margin: 3px;" in window
    # The needle and its pin are the reading ink; only a hairline of the panel's own surface sets them off the ticks.
    needle = body_of(css, ".cp-needle")
    assert "background: var(--cp-ink);" in needle and "box-shadow: 0 0 0 1.5px var(--cp-surface);" in needle
    assert "background: var(--cp-ink);" in body_of(css, ".cp-pin-dot")
    # A press and a drag choose through the scale itself; a pointer never clicks a level twice (the click that is left
    # is the keyboard's and assistive technology's, which carry no pointer).
    assert "event.detail === 0" in source and "setPointerCapture" in source
    # Letting go chooses, a drag in between does not: the Host is told once, from pointer-up, never from a move.
    move = source[source.index("function onPointerMove"):source.index("function onPointerUp")]
    assert "commit(" not in move and "onChange(" not in move


def test_the_scale_never_restyles_per_frame() -> None:
    """The first build carried the needle's place in an animated custom property that every tick read: in this app that
    restyled ~190 elements and cost ~3 ms on every frame of the 320ms spring (the app's global sheets make each
    restyled element dear, and an inherited property restyles them all). Now the ticks stand still and the needle,
    its pin and the window that reveals the lit copy of the ticks are `transform` transitions, which the compositor
    runs: a level change restyles nothing per frame."""
    source = read(COMPONENTS / "ReasoningControl.tsx")
    css = stripped(read(SHEET))
    assert "@property" not in css
    # No custom property carries the needle's place, and none is transitioned.
    assert "--cp-pos" not in source and "--cp-pos" not in css and "--cp-peek" not in css
    reasoning = re.compile(r"\.cp-(?:steps|step|scale|ticks|lit|lit-ticks|slide|needle|pin|pin-dot)(?![\w-])")
    animated: set[str] = set()
    for selector, body in rules(css):
        if not reasoning.search(selector):
            continue
        for value in re.findall(r"(?<![\w-])transition\s*:\s*([^;]+);", body):
            animated |= set(re.findall(r"(?:^|,)\s*([a-z-]+)\s+var\(", value))
    # Only what the compositor or a few elements can carry: never a size or a position, which would be laid out on
    # every frame, and never a custom property.
    assert animated and animated <= {"transform", "scale", "color", "background-color"}, animated
    # The ticks are drawn once, whatever the needle does (only a hovered level changes them).
    assert "const Ticks = memo(" in source
    # The needle is placed by the scale's own geometry, as inline transforms on the few elements that move.
    for place in ("translateX(${(place - 1) * 100}%)", "translateX(${(1 - place) * 100}%)", "translateX(${place * 100}%)",
                  "scaleY(${height / NEEDLE_MAX})", "translateY(${-height}px)"):
        assert place in source, place


def test_text_is_on_the_ladder_and_above_the_floor() -> None:
    css = stripped(read(SHEET))
    sizes = {float(value) for value in re.findall(r"font-size:\s*([\d.]+)px", css)}
    assert sizes <= {11.5, 12, 13}, sizes
    weights = {int(value) for value in re.findall(r"font-weight:\s*(\d{3})", css)}
    assert weights <= {400, 500, 600}, weights

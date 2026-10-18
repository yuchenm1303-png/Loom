"""The scale the renderer's stylesheets are written in.

The source used to ask for 32 font weights (every ten from 520 to 800), 70 font sizes (8.3, 8.35, 8.4, 8.45 ...)
and an icon stroke for every hand that drew a call site (1.6, 1.65, 1.7, 1.75, 1.8, 1.85, 1.9, 2). Two or three of
each can be told apart on a screen; the rest is noise a person feels as "assembled" rather than "designed", and each
weight is also a font instance the app has to make (src/fontWarmup.ts). These tests keep the scale from growing back.
They read the stylesheets as text, like the other source contracts.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"

# Text, label, heading, strong.
WEIGHTS = {400, 500, 600, 650}
# From the readability floor up: caption, meta, UI, body, title; then the display sizes.
SIZES = {11.5, 12, 13, 14, 15, 16, 18, 20, 24, 28, 32}
# Declarations that still set text below the 11.5px floor (typography-scale.css says why that is a problem). They were
# left as they were when the ladder was made, each is a panel to be looked at on its own, so this number may only go down.
BELOW_THE_FLOOR = 338


def stylesheets() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.css") if "node_modules" not in path.parts)


def declarations(prop: str) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in stylesheets():
        css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        found.extend((path, value) for value in re.findall(rf"(?<![\w-]){prop}\s*:\s*([^;}}]+)", css))
    return found


def test_the_product_is_made_of_four_weights() -> None:
    weights = {int(value.split()[0]) for _, value in declarations("font-weight") if re.match(r"\d{3}\b", value.strip())}
    assert weights <= WEIGHTS, sorted(weights - WEIGHTS)
    # And the app makes exactly those instances while it is idle.
    warmup = (SRC / "fontWarmup.ts").read_text(encoding="utf-8")
    listed = {int(weight) for weight in re.findall(r"\d{3}", re.search(r"UI_FONT_WEIGHTS[^=]*=\s*\[(.*?)\]", warmup, flags=re.S).group(1))}
    assert listed == WEIGHTS


def test_sizes_from_the_floor_up_are_on_the_ladder() -> None:
    off: dict[float, list[str]] = {}
    for path, value in declarations("font-size"):
        match = re.match(r"([\d.]+)px\b", value.strip())
        if not match:
            continue  # em and var() sizes follow the size they sit in
        size = float(match.group(1))
        if size >= 11.5 and size not in SIZES:
            off.setdefault(size, []).append(path.name)
    assert not off, {size: sorted(set(names))[:4] for size, names in sorted(off.items())}


def test_text_below_the_floor_only_goes_down() -> None:
    below = [
        1 for _, value in declarations("font-size")
        if (match := re.match(r"([\d.]+)px\b", value.strip())) and float(match.group(1)) < 11.5
    ]
    assert len(below) <= BELOW_THE_FLOOR, f"{len(below)} declarations set text below 11.5px (was {BELOW_THE_FLOOR}); raise one, do not add one"


def test_the_icon_family_has_one_stroke() -> None:
    css = (SRC / "components" / "renderer-crispness.css").read_text(encoding="utf-8")

    # Glyphs drawn between 1.6 and 2 are drawn at 1.75; the thin and the heavy ones are deliberate.
    rule = re.search(r":is\(\.loom-icon, svg\.lucide\):is\((.*?)\)\s*\{\s*stroke-width:\s*1\.75;", css, flags=re.S)
    assert rule, "renderer-crispness.css draws one stroke for the icon family"
    asked = set(re.findall(r'\[stroke-width="([\d.]+)"\]', rule.group(1)))
    assert asked == {"1.6", "1.65", "1.7", "1.75", "1.8", "1.85", "1.9", "2"}

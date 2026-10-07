"""Allow shared icon motion without permitting late layout override layers."""
import re


def owns_component_layout(path, selectors, css):
    if not selectors.search(css):
        return False
    if path.name != "icon-motion.css":
        return True
    # Shared motion may reference component descendants, but must not own their
    # dimensions, placement, typography or surfaces. Validate every matching rule
    # rather than exempting the stylesheet from ownership checks wholesale.
    motion = {"transition", "transition-duration", "transition-timing-function",
              "animation", "transform", "transform-box", "transform-origin",
              "translate", "rotate", "filter", "overflow"}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not selectors.search(selector):
            continue
        properties = set(re.findall(r"([\w-]+)\s*:", body))
        assert properties <= motion, (selector, properties - motion)
        # Button-level rules may adjust timing only. Movement and SVG overflow
        # must target glyphs, never the component container itself.
        if not properties <= {"transition-duration", "transition-timing-function"}:
            assert any(glyph in selector for glyph in ("svg", "lucide", "icon", "chevron")), selector
    return False

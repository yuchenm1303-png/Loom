from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "desktop-react" / "src" / "components" / "TranscriptScrollController.tsx"


def test_thread_switch_uses_forced_bottom_settle_window() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")

    assert "const forceBottomRef = useRef(false);" in source
    assert "const forceSettleFrameRef = useRef<number | null>(null);" in source
    assert "const forceBottomAfterThreadSwitch = (scroller: HTMLDivElement) => {" in source
    assert "forceBottomRef.current = true;" in source
    assert source.count("scroller.scrollTop = scroller.scrollHeight;") >= 4

    # A thread replacement can itself emit scroll events while the old scrollTop
    # is being clamped to the new content. Those passive events must not cancel
    # the explicit thread-switch jump.
    assert "if (isPanelResizeActive() || forceBottomRef.current) return;" in source
    assert "forceBottomAfterThreadSwitch(scroller);" in source

    # The regular item-update effect must not compete with the dedicated
    # thread-switch transaction.
    assert "if (!threadChanged && stickToBottomRef.current) scheduleBottomSync(scroller);" in source


def test_same_thread_user_scroll_still_controls_auto_follow() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")

    # Once the short switch transaction settles, normal user scrolling retains
    # the existing near-bottom policy instead of permanently forcing follow.
    assert "forceBottomRef.current = false;" in source
    assert "stickToBottomRef.current = isNearBottom(scroller);" in source
    assert "if (stickToBottomRef.current) scheduleBottomSync(scroller);" in source

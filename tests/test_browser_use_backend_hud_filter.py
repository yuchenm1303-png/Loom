"""Loom's own page HUD must not reach the model as page content."""

from app.agent_runtime.browser_use_backend import _strip_page_hud

PAGE = """[19]<button id=checkout />
\tCheckout
Open Shadow
\t[3]<input id=q />
\tQuantity
Shadow End
Open Shadow
\tLoom 正在控制浏览器
\t目标置信度
\tbrowser
\t执行坐标
\t1280, 736
\t动作来源
\tbrowser runtime
Shadow End"""


def test_the_hud_shadow_block_is_removed_and_page_shadow_roots_stay():
    stripped = _strip_page_hud(PAGE)
    assert "正在控制" not in stripped and "执行坐标" not in stripped
    assert "[3]<input id=q />" in stripped and "Checkout" in stripped
    assert stripped.count("Open Shadow") == 1


def test_a_page_that_merely_mentions_one_label_is_untouched():
    page = "Open Shadow\n\t目标置信度 is a word on this page\nShadow End"
    assert _strip_page_hud(page) == page

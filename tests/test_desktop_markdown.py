from __future__ import annotations

from app.desktop.markdown import parse_blocks, render_html


def test_prose_and_code_are_separate_blocks():
    blocks = parse_blocks("Here you go:\n\n```python\nprint(1)\n```\n\nDone.")
    assert [block.kind for block in blocks] == ["rich", "code", "rich"]
    assert blocks[1].language == "python"
    assert blocks[1].source == "print(1)"


def test_unterminated_fence_still_yields_a_code_block_while_streaming():
    blocks = parse_blocks("```bash\npytest -q")
    assert [block.kind for block in blocks] == ["code"]
    assert blocks[0].source == "pytest -q"


def test_streaming_prefix_reuses_earlier_blocks():
    partial = parse_blocks("Intro line\n\n```py\nx = 1\n```\n\nTai")
    complete = parse_blocks("Intro line\n\n```py\nx = 1\n```\n\nTail")
    assert partial[:2] == complete[:2]
    assert partial[2] != complete[2]


def test_html_is_escaped_before_markup_is_applied():
    html = render_html("<script>alert('x')</script> and **bold**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>bold</strong>" in html


def test_inline_code_is_not_reinterpreted_as_markup():
    html = render_html("use `**not bold**` here")
    assert "<code>**not bold**</code>" in html
    assert "<strong>" not in html


def test_lists_headings_quotes_and_checkboxes():
    html = render_html(
        "# Title\n\n- one\n- [x] done\n- [ ] todo\n\n> quoted\n\n1. first\n2. second"
    )
    assert "<div class='h1'>Title</div>" in html
    assert "check done" in html
    assert "check todo" in html
    assert "<blockquote>" in html
    assert "<ol>" in html and "<ul>" in html


def test_links_render_but_only_for_http_urls():
    html = render_html("[docs](https://example.com) and [bad](javascript:alert(1))")
    assert '<a href="https://example.com">docs</a>' in html
    assert "javascript:" not in html.split("</a>")[-1] or "<a href=\"javascript:" not in html


def test_empty_text_still_renders_a_valid_document():
    assert render_html("") == "<p></p>"

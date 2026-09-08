from __future__ import annotations

from app.desktop.markdown import parse_blocks, render_html, split_reasoning


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


def test_inline_thinking_is_separated_from_the_reply():
    thinking, body, live = split_reasoning("<think>weigh it up</think>Here is the answer.")
    assert thinking == "weigh it up"
    assert body == "Here is the answer."
    assert live is False


def test_unclosed_thinking_is_all_reasoning_while_it_streams():
    thinking, body, live = split_reasoning("<think>still weighing")
    assert (thinking, body, live) == ("still weighing", "", True)


def test_a_reply_that_starts_mid_thought_is_still_split():
    # Some reasoning models emit only the closing tag.
    assert split_reasoning("weighing</think>answer") == ("weighing", "answer", False)


def test_several_thinking_passes_are_collected_in_order():
    thinking, body, _live = split_reasoning("a<think>one</think>b<think>two</think>c")
    assert thinking == "one\n\ntwo"
    assert body == "abc"


def test_a_half_arrived_opening_tag_never_shows_as_text():
    # Deltas split anywhere, including inside the tag itself.
    assert split_reasoning("Ready <thi") == ("", "Ready", False)
    assert split_reasoning("plain text") == ("", "plain text", False)

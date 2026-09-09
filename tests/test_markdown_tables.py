from app.desktop import markdown


def _rich_html(source: str) -> str:
    return "".join(block.html for block in markdown.parse_blocks(source) if block.kind == "rich")


def test_gfm_table_is_rendered_as_real_table() -> None:
    source = """| 项目 | 大小 |
| --- | ---: |
| 总容量 | **200 GB** |
| 已使用 | 161.7 GB |
| 剩余可用 | 38.3 GB |
"""

    rendered = _rich_html(source)

    assert "<table class='mdTable'" in rendered
    assert "<th align='left'>项目</th>" in rendered
    assert "<th align='right'>大小</th>" in rendered
    assert "<td align='right'><strong>200 GB</strong></td>" in rendered
    assert "| --- |" not in rendered


def test_table_auto_aligns_numeric_column_when_markers_are_plain() -> None:
    source = """| Name | Value |
| --- | --- |
| total | 200 GB |
| free | 38.3 GB |
"""

    rendered = _rich_html(source)

    assert "<th align='right'>Value</th>" in rendered
    assert "<td align='right'>38.3 GB</td>" in rendered


def test_warning_emoji_becomes_quiet_callout() -> None:
    rendered = _rich_html("⚠️ **提醒**：剩余空间已经不多。")

    assert "class='callout warning'" in rendered
    assert "⚠" not in rendered
    assert "<strong>提醒</strong>" in rendered


def test_escaped_pipe_stays_inside_table_cell() -> None:
    source = """| Command | Note |
| --- | --- |
| `a \\| b` | pipe |
"""

    rendered = _rich_html(source)

    assert "<code>a | b</code>" in rendered
    assert rendered.count("<td") == 2

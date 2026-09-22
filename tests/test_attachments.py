"""Attachment staging: what reaches the model, and what stays a path."""

from __future__ import annotations

import base64
import zipfile

import pytest

from app.agent_runtime.turn_input import normalize_turn_input, turn_input_text
from app.ai import ImagePart, TextPart
from app.attachments import (
    MAX_ATTACHMENTS,
    AttachmentError,
    attachment_manifest,
    build_turn_content,
    safe_name,
    stage_attachments,
)


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _source(tmp_path, name: str, data: bytes = b"hello") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_a_file_is_copied_into_the_workspace_and_described_by_path(tmp_path, workspace):
    source = _source(tmp_path, "report.csv", b"a,b\n1,2\n")

    staged = stage_attachments(
        [{"path": source, "name": "report.csv"}],
        workspace=workspace,
        turn_id="turn-1234abcd",
    )

    assert len(staged) == 1
    entry = staged[0]
    assert entry.kind == "file"
    assert entry.path.read_bytes() == b"a,b\n1,2\n"
    # Workspace-relative, because that is the shape the agent's file tools take.
    assert entry.relative_path.startswith(".loom/attachments/")
    assert (workspace / entry.relative_path).is_file()


def test_a_non_image_never_becomes_model_content(tmp_path, workspace):
    source = _source(tmp_path, "notes.txt", b"x" * 4096)
    staged = stage_attachments(
        [{"path": source}], workspace=workspace, turn_id="turn-1"
    )

    content = build_turn_content("summarise this", staged)

    assert isinstance(content, str)
    assert "notes.txt" in content
    assert staged[0].relative_path in content
    # The bytes stay on disk: the point of a path is not shipping the payload.
    assert "xxxx" not in content


def test_an_image_becomes_an_image_part_alongside_the_text(tmp_path, workspace):
    source = _source(tmp_path, "shot.png", PNG_1PX)
    staged = stage_attachments(
        [{"path": source}], workspace=workspace, turn_id="turn-1"
    )

    content = build_turn_content("what is this", staged)

    assert isinstance(content, tuple)
    text_parts = [part for part in content if isinstance(part, TextPart)]
    image_parts = [part for part in content if isinstance(part, ImagePart)]
    assert len(image_parts) == 1
    assert image_parts[0].image_url.startswith("data:image/png;base64,")
    assert "what is this" in text_parts[0].text
    assert "shot.png" in text_parts[0].text


def test_an_attachment_alone_is_a_complete_message(tmp_path, workspace):
    source = _source(tmp_path, "shot.png", PNG_1PX)
    staged = stage_attachments([{"path": source}], workspace=workspace, turn_id="t")

    content = build_turn_content("", staged)

    assert isinstance(content, tuple)
    assert any(isinstance(part, ImagePart) for part in content)


def test_text_without_attachments_is_unchanged(workspace):
    assert build_turn_content("  hello  ", ()) == "hello"


def test_an_empty_message_is_refused(workspace):
    with pytest.raises(AttachmentError):
        build_turn_content("   ", ())


def test_images_are_refused_when_the_model_cannot_read_them(tmp_path, workspace):
    source = _source(tmp_path, "shot.png", PNG_1PX)

    with pytest.raises(AttachmentError, match="cannot read images"):
        stage_attachments(
            [{"path": source}],
            workspace=workspace,
            turn_id="t",
            allow_images=False,
        )


def test_a_text_file_is_still_accepted_by_a_text_only_model(tmp_path, workspace):
    source = _source(tmp_path, "notes.txt")

    staged = stage_attachments(
        [{"path": source}], workspace=workspace, turn_id="t", allow_images=False
    )

    assert staged[0].kind == "file"


def test_an_oversized_image_is_refused_with_its_size(tmp_path, workspace):
    source = _source(tmp_path, "huge.png", b"\0" * (9 * 1024 * 1024))

    with pytest.raises(AttachmentError, match="MB"):
        stage_attachments([{"path": source}], workspace=workspace, turn_id="t")


def test_too_many_attachments_are_refused(tmp_path, workspace):
    sources = [
        {"path": _source(tmp_path, f"file-{index}.txt")}
        for index in range(MAX_ATTACHMENTS + 1)
    ]

    with pytest.raises(AttachmentError, match="at most"):
        stage_attachments(sources, workspace=workspace, turn_id="t")


def test_a_missing_source_is_refused_rather_than_silently_dropped(workspace):
    with pytest.raises(AttachmentError, match="does not exist"):
        stage_attachments(
            [{"path": "no/such/file.txt"}], workspace=workspace, turn_id="t"
        )


def test_a_client_supplied_name_cannot_escape_the_attachment_directory(tmp_path, workspace):
    source = _source(tmp_path, "innocent.txt")

    staged = stage_attachments(
        [{"path": source, "name": "../../../etc/passwd"}],
        workspace=workspace,
        turn_id="t",
    )

    assert staged[0].path.is_relative_to(workspace)
    assert ".." not in staged[0].relative_path


def test_two_files_with_the_same_name_do_not_overwrite_each_other(tmp_path, workspace):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "notes.txt").write_bytes(b"first")
    (second / "notes.txt").write_bytes(b"second")

    staged = stage_attachments(
        [{"path": str(first / "notes.txt")}, {"path": str(second / "notes.txt")}],
        workspace=workspace,
        turn_id="t",
    )

    assert staged[0].path != staged[1].path
    assert staged[0].path.read_bytes() == b"first"
    assert staged[1].path.read_bytes() == b"second"


def test_a_cjk_filename_survives_sanitisation():
    assert safe_name("季度报告 v2.pdf") == "季度报告_v2.pdf"


def test_the_manifest_names_every_attachment(tmp_path, workspace):
    staged = stage_attachments(
        [
            {"path": _source(tmp_path, "shot.png", PNG_1PX)},
            {"path": _source(tmp_path, "data.csv")},
        ],
        workspace=workspace,
        turn_id="t",
    )

    manifest = attachment_manifest(staged)

    assert "shot.png" in manifest
    assert "data.csv" in manifest
    assert "file tools" in manifest




def test_docx_gets_a_readable_text_companion(tmp_path, workspace):
    source = tmp_path / "report.docx"
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Quarterly result</w:t></w:r></w:p>
    <w:p><w:r><w:t>Revenue grew 12 percent.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document)

    staged = stage_attachments([{"path": str(source)}], workspace=workspace, turn_id="t")
    entry = staged[0]

    assert entry.extracted_relative_path.endswith(".docx.extracted.txt")
    extracted = workspace / entry.extracted_relative_path
    assert extracted.is_file()
    assert "Quarterly result" in extracted.read_text(encoding="utf-8")
    manifest = attachment_manifest(staged)
    assert "extracted text:" in manifest
    assert entry.extracted_relative_path in manifest


def test_xlsx_gets_a_tabular_text_companion(tmp_path, workspace):
    source = tmp_path / "book.xlsx"
    shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
  <si><t>Name</t></si><si><t>Alice</t></si>
</sst>"""
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row>
    <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>7</v></c></row>
  </sheetData>
</worksheet>"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    staged = stage_attachments([{"path": str(source)}], workspace=workspace, turn_id="t")
    extracted = (workspace / staged[0].extracted_relative_path).read_text(encoding="utf-8")

    assert "Name\t42" in extracted
    assert "Alice\t7" in extracted

# ---- turn input normalisation --------------------------------------------


def test_plain_text_input_stays_a_plain_string():
    content, text = normalize_turn_input("  do the thing  ")
    assert content == "do the thing"
    assert text == "do the thing"


def test_a_single_text_part_collapses_back_to_a_string():
    content, text = normalize_turn_input((TextPart("hello"),))
    assert content == "hello"
    assert text == "hello"


def test_multipart_input_keeps_its_parts_and_yields_readable_text():
    parts = (TextPart("look"), ImagePart(image_url="data:image/png;base64,AA"))
    content, text = normalize_turn_input(parts)
    assert content == parts
    assert text == "look"


def test_image_only_input_still_produces_journal_text():
    parts = (ImagePart(image_url="data:image/png;base64,AA"),)
    _content, text = normalize_turn_input(parts)
    assert text == "[1 image]"
    assert turn_input_text(parts) == "[1 image]"


@pytest.mark.parametrize("value", ["", "   ", (), (TextPart(" "),)])
def test_empty_input_is_refused(value):
    with pytest.raises(ValueError):
        normalize_turn_input(value)

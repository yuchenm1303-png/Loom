"""Attachment enhancement must not escape its workspace or inflate unbounded Office content."""
import zipfile

import pytest

from app import attachments


@pytest.mark.parametrize("member", ["word/document.xml", "xl/sharedStrings.xml", "ppt/slides/slide1.xml", "content.xml"])
def test_office_expansion_is_refused_before_reading_members(tmp_path, monkeypatch, member):
    suffix = {"word/document.xml": ".docx", "xl/sharedStrings.xml": ".xlsx", "ppt/slides/slide1.xml": ".pptx", "content.xml": ".ods"}[member]
    source = tmp_path / ("large" + suffix)
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, "a" * 4096)
    monkeypatch.setattr(attachments, "MAX_DOCUMENT_XML_BYTES", 1024)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("oversized content must be rejected before decompression")

    monkeypatch.setattr(zipfile.ZipFile, "read", unexpected_read)
    assert attachments.extract_attachment_text(source) == ""
    staged = attachments.stage_attachments([{"path": str(source)}], workspace=tmp_path / "workspace", turn_id="turn")
    assert staged[0].path.read_bytes() == source.read_bytes(), "the original attachment remains usable"
    assert not staged[0].extracted_relative_path


def test_repeated_xlsx_shared_strings_are_bounded_while_building_output(tmp_path, monkeypatch):
    source = tmp_path / "repeat.xlsx"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>{"x" * 200}</t></si></sst>')
        cells = '<c t="s"><v>0</v></c>' * 100
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData><row>{cells}</row></sheetData></worksheet>')
    monkeypatch.setattr(attachments, "MAX_EXTRACTED_TEXT_CHARS", 100)
    # Inspect the pre-truncation chunks too: a final slice alone does not bound construction.
    original = attachments._bounded_text
    chunks = []

    def bounded(parts):
        chunks.extend(parts)
        return original(parts)

    monkeypatch.setattr(attachments, "_bounded_text", bounded)
    result = attachments.extract_attachment_text(source)
    assert result.startswith("Sheet 1\n") and "truncated" in result
    assert sum(map(len, chunks)) <= 110


def test_unread_embedded_media_does_not_disable_small_document_text(tmp_path, monkeypatch):
    source = tmp_path / "photo.docx"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/large.png", "a" * 4096)
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:t>caption</w:t></w:p></w:document>')
    monkeypatch.setattr(attachments, "MAX_DOCUMENT_XML_BYTES", 1024)
    monkeypatch.setattr(attachments, "MAX_DOCUMENT_EXPANDED_BYTES", 2048)
    assert attachments.extract_attachment_text(source) == "caption"


@pytest.mark.parametrize("turn", ["../other", "../../..", "/outside", "C:\\temp", ".."])
def test_staging_rejects_path_shaped_turn_identifiers(tmp_path, turn):
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")
    with pytest.raises(attachments.AttachmentError, match="unsafe path"):
        attachments.stage_attachments([{"path": str(source)}], workspace=tmp_path / "workspace", turn_id=turn)
    assert not (tmp_path / "workspace").exists()

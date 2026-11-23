"""Local attachment extraction and Incoming handoff regressions."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from PyPDF2 import PageObject, PdfWriter
from PyPDF2.generic import DecodedStreamObject, DictionaryObject, NameObject
from docx import Document

from distr.core.db import Chat, WhatsAppMessage
from distr.core.incoming import attachment_text, mail_attachments, media, proposals, transcription
from tests.core.test_incoming_first_turn_transcripts import _handoff
from tests.core.test_incoming_media import store as store


@pytest.fixture
def word_document(tmp_path):
    document = Document()
    document.add_paragraph("Updated agreement")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Payment terms"
    path = tmp_path / "agreement.docx"
    document.save(path)
    return path


def _pdf(tmp_path, *, pages=1, text_layer=False, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=150, height=150)
        if text_layer:
            page = writer.pages[-1]
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 100 Td (Readable terms) Tj ET")
            page[NameObject("/Contents")] = stream
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): DictionaryObject({
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }),
                }),
            })
    if password is not None:
        writer.encrypt(password)
    path = tmp_path / "source.pdf"
    writer.write(path)
    return path.read_bytes()


def test_docx_reads_paragraphs_and_table_cells(word_document):
    result = attachment_text.extract_attachment_file(word_document)
    assert (result["status"], result["engine"], result["text"]) == (
        "ok", "python-docx", "Updated agreement Payment terms",
    )


def test_doc_uses_textutil_and_cleans_up(monkeypatch):
    temp_files = []

    def convert(command, *, check, capture_output, timeout):
        assert command[:-1] == ["/usr/bin/textutil", "-convert", "txt", "-stdout"]
        assert check and capture_output and timeout == 30
        source = Path(command[-1])
        assert source.suffix == ".doc" and source.read_bytes() == b"legacy document"
        assert source.parent.stat().st_mode & 0o077 == 0
        temp_files.append(source)
        return SimpleNamespace(stdout=b"Legacy\n terms")

    monkeypatch.setattr(attachment_text.shutil, "which", {"textutil": "/usr/bin/textutil"}.get)
    monkeypatch.setattr(attachment_text.subprocess, "run", convert)
    result = attachment_text.extract_attachment_bytes(b"legacy document", filename="terms.doc")
    assert (result["status"], result["engine"], result["text"]) == ("ok", "textutil", "Legacy terms")
    assert not temp_files[0].parent.exists()


def test_doc_reports_missing_converters(monkeypatch):
    monkeypatch.setattr(attachment_text.shutil, "which", lambda _: None)
    result = attachment_text.extract_attachment_bytes(b"legacy document", mime_type="application/msword")
    assert result["status"] == "missing_dependency"
    assert result["reason"] == "Missing dependency: textutil or antiword is needed to read .doc files."
    assert attachment_text.apply_extraction({}, result)["missing_dependencies"] == ["textutil", "antiword"]


def _mock_ocr(monkeypatch, binaries, ocr=None):
    real_import = attachment_text._optional_module
    modules = {"pillow_heif": None, "pytesseract": SimpleNamespace(image_to_string=ocr) if ocr else None}
    monkeypatch.setattr(attachment_text, "_optional_module",
                        lambda name: modules[name] if name in modules else real_import(name))
    monkeypatch.setattr(attachment_text.shutil, "which", binaries.get)


def test_heic_reports_missing_converters(monkeypatch):
    _mock_ocr(monkeypatch, {})
    result = attachment_text.extract_attachment_bytes(b"heic bytes", filename="photo.heic")
    assert result["status"] == "missing_dependency"
    assert result["missing_dependencies"] == ["pillow-heif", "sips"]
    assert all(name in result["reason"] for name in result["missing_dependencies"])


def test_heif_uses_sips_then_image_ocr(monkeypatch):
    ocr = Mock(return_value="Photo text")
    _mock_ocr(monkeypatch, {"sips": "/usr/bin/sips", "tesseract": "/local/tesseract"}, ocr)
    temp_files = []

    def convert(command, *, check, capture_output, timeout):
        assert command[:4] == ["/usr/bin/sips", "-s", "format", "jpeg"]
        assert command[5] == "--out" and check and capture_output and timeout == 30
        source, jpeg = Path(command[4]), Path(command[6])
        assert source.suffix == ".heif" and source.read_bytes() == b"heif bytes"
        assert jpeg.suffix == ".jpg" and jpeg.parent == source.parent
        Image.new("RGB", (2, 2)).save(jpeg)
        temp_files.extend((source, jpeg))

    monkeypatch.setattr(attachment_text.subprocess, "run", convert)
    result = attachment_text.extract_attachment_bytes(b"heif bytes", mime_type="image/heif")
    assert (result["status"], result["engine"], result["text"]) == ("ok", "tesseract", "Photo text")
    ocr.assert_called_once()
    assert ocr.call_args.args[0].format == "JPEG"
    assert ocr.call_args.kwargs == {"timeout": attachment_text.OCR_TIMEOUT_SECONDS}
    assert all(not path.exists() for path in temp_files)


def test_image_reports_missing_ocr_dependencies(monkeypatch):
    _mock_ocr(monkeypatch, {})
    result = attachment_text.extract_attachment_bytes(b"png bytes", filename="photo.png")
    assert result["status"] == "missing_dependency"
    assert result["missing_dependencies"] == ["tesseract", "pytesseract"]


def test_encrypted_pdf_with_failed_decrypt_is_skipped(tmp_path, monkeypatch):
    render = Mock()
    monkeypatch.setattr(attachment_text.subprocess, "run", render)
    result = attachment_text.extract_pdf_text(_pdf(tmp_path, password="fixture-password", text_layer=True))
    assert (result["status"], result["reason"]) == ("skipped", "PDF is encrypted.")
    render.assert_not_called()


def test_partial_pdf_retains_extracted_text(tmp_path, monkeypatch):
    data = _pdf(tmp_path, pages=2, text_layer=True)
    assert attachment_text.extract_pdf_text(data)["text"] == "Readable terms Readable terms"
    extract = PageObject.extract_text
    successful_pages = []

    def extract_page(page):
        if successful_pages:
            raise ValueError("Unreadable page")
        successful_pages.append(page)
        return extract(page)

    monkeypatch.setattr(PageObject, "extract_text", extract_page)
    result = attachment_text.extract_pdf_text(data)
    assert result["status"] == "partial"
    assert result["engine"] == "PyPDF2"
    assert result["reason"] == "1 of 2 pages could not be read."
    record = attachment_text.apply_extraction({"name": "source.pdf"}, result)
    prompt = mail_attachments.prompt_attachments_for_item({"attachments": [record]})
    assert prompt[0]["extraction_status"] == "partial"
    assert prompt[0]["extracted_text"] == "Readable terms"


@pytest.mark.parametrize("fail_first_page", [False, True])
def test_blank_pdf_renders_capped_pages_and_runs_ocr(tmp_path, monkeypatch, fail_first_page):
    data = _pdf(tmp_path, pages=12)
    visited = []

    def read_page(filename, *, timeout):
        assert timeout == attachment_text.OCR_TIMEOUT_SECONDS
        page = Path(filename)
        assert page.is_file()
        visited.append(page)
        if fail_first_page and page.name == "page-1.png":
            raise RuntimeError("OCR timeout")
        return f"Text {page.stem}"

    def render(command, *, check, capture_output, timeout):
        assert command[:-2] == ["/local/pdftoppm", "-r", "200", "-png", "-f", "1", "-l", "10"]
        assert check and capture_output and timeout == 60
        assert Path(command[-2]).read_bytes() == data
        prefix = Path(command[-1])
        for number in range(1, 11):
            prefix.with_name(f"{prefix.name}-{number}.png").touch()

    _mock_ocr(monkeypatch, {"pdftoppm": "/local/pdftoppm", "tesseract": "/local/tesseract"}, read_page)
    monkeypatch.setattr(attachment_text.subprocess, "run", render)
    result = attachment_text.extract_pdf_text(data)
    assert result["engine"] == "pdftoppm+tesseract"
    assert result["pages"] == 12
    assert result["status"] == ("partial" if fail_first_page else "ok")
    assert result["reason"] == ("1 of 10 pages could not be read." if fail_first_page else "")
    assert result["text"].endswith("Text page-10")
    assert [page.name for page in visited] == [f"page-{n}.png" for n in range(1, 11)]
    assert not visited[0].parent.exists()


def test_blank_pdf_reports_missing_pdftoppm(tmp_path, monkeypatch):
    _mock_ocr(monkeypatch, {"tesseract": "/local/tesseract"}, Mock())
    result = attachment_text.extract_pdf_text(_pdf(tmp_path))
    assert result["status"] == "missing_dependency"
    assert result["reason"] == "PDF has no text layer; OCR needs pdftoppm."
    assert result["missing_dependencies"] == ["pdftoppm"]


def test_over_cap_mail_files_stay_visible_only_in_skipped_rows(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    item = {
        "message_id": "mail-message",
        "attachments": [
            {"name": f"file-{index}.txt", "mime_type": "text/plain", "size": 4}
            for index in range(17)
        ],
    }
    skipped = mail_attachments.skipped_item_attachments(item)
    assert skipped == [
        {"name": f"file-{index}.txt", "mime_type": "text/plain", "size": 4, "message_id": "mail-message",
         "download_status": "skipped", "extraction_status": "skipped",
         "extraction_reason": "Not processed: this message has more than 15 attachments."}
        for index in (15, 16)
    ]
    prompt = mail_attachments.prompt_attachments_for_item(item)
    assert len(prompt) == 17 and all("path" not in row for row in prompt[15:])
    assert [row["extraction_reason"] for row in prompt[15:]] == [row["extraction_reason"] for row in skipped]
    download = Mock(side_effect=lambda row: (b"file", "text/plain", row["name"]))
    conversation = {"source": "gmail", "items": [item]}
    stored = mail_attachments.enrich_conversation_mail_attachments(conversation, downloader=download)
    assert len(stored) == len(item["attachments"]) == download.call_count == 15
    assert all(row["download_status"] == "stored" for row in item["attachments"])
    assert item["skipped_attachments"] == skipped
    assert mail_attachments.prompt_attachments_for_item(item)[15:] == prompt[15:]
    assert [record.getMessage() for record in caplog.records if record.levelname == "WARNING"] == [
        "Mail message mail-message skipped 2 attachments above the limit.",
    ]


@pytest.mark.parametrize("cached", [False, True])
def test_whatsapp_extracts_documents_and_ignores_audio(store, tmp_path, word_document, cached):
    sessions, _ = store
    audio = tmp_path / "voice.oga"
    audio.write_bytes(b"audio fixture")
    jid = "attachment-test@g.us"
    with sessions() as db:
        for path, kind in ((word_document, "document"), (audio, "audio")):
            db.add(WhatsAppMessage(message_id=path.stem, jid=jid, media_type=kind, media_filename=path.name,
                                   media_mime_type="application/octet-stream", media_local_path=str(path)))
    proposal = {"id": "local:whatsapp:extraction", "source": "whatsapp", "source_thread_id": jid,
                "message_ids": [word_document.stem, audio.stem]}
    if cached:
        identity = f"whatsapp:{word_document.stem}"
        destination = media._destination(media._root(7, proposal["id"]), identity, word_document.name)
        media.media_cache.copy_original(word_document, destination)
        media.media_cache.remember(media._record(destination, word_document.name, "", identity, kind="document"))
        word_document.unlink()
    result = media.ensure_proposal_attachments(proposal, 7)
    assert result["failures"] == []
    records = {record["name"]: record for record in result["attachments"]}
    document = records[word_document.name]
    assert document["extraction_status"] == "ok"
    assert document["extraction_engine"] == "python-docx"
    assert document["extracted_text"] == "Updated agreement Payment terms"
    assert "extraction_status" not in records[audio.name]
    assert media.ensure_proposal_attachments(proposal, 7) == result


@pytest.mark.parametrize("with_transcripts", [False, True])
def test_proposal_thread_saves_and_returns_transcript_report(store, monkeypatch, with_transcripts):
    chat_id, proposal, _ = _handoff(store, monkeypatch, [])
    monkeypatch.setattr("distr.core.incoming.project_links.proposal_in_scope", lambda _: True)
    monkeypatch.setattr("distr.core.kanban.proposal_tickets.bind_proposal_thread_ticket", Mock())
    attachment_failures = [{"name": "brief.pdf", "error": "Source unavailable"}]
    monkeypatch.setattr(media, "ensure_proposal_attachments",
                        Mock(return_value={"attachments": [], "failures": attachment_failures}))
    report = {"ready": 0, "empty": 0, "failed": []}
    if with_transcripts:
        report = {"ready": 1, "empty": 1, "failed": [{"id": "sample", "name": "voice.oga"}]}
    prepare = Mock(return_value=report)
    monkeypatch.setattr(transcription, "prepare_thread_transcripts", prepare)
    assert proposals.proposal_thread_attachment_report(chat_id)["transcript_report"] == {}
    assert proposals.create_proposal_thread(proposal) == chat_id
    prepare.assert_called_once_with(chat_id)
    with store[0]() as db:
        assert json.loads(db.get(Chat, chat_id).params)["incoming_transcript_report"] == report
    assert proposals.proposal_thread_attachment_report(chat_id) == {
        "failed_attachments": attachment_failures, "ticket_file_failures": [], "transcript_report": report,
    }

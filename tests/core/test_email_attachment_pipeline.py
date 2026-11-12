"""Email attachment pipeline: extract, triage prompt, proposal, thread context.

Downloads and providers are mocked. Tests do not touch the live settings.db or send mail.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from distr.core.incoming.attachment_text import extract_attachment_bytes, extract_pdf_text
from distr.core.incoming.mail_attachments import (
    collect_conversation_attachments,
    enrich_conversation_mail_attachments,
    prompt_attachments_for_item,
    store_mail_attachment,
    work_intake_attachments,
)
from distr.core.agent.services.integrations import google_workspace as gw


def _minimal_pdf(text: str = "Crystal Logic support terms") -> bytes:
    payload = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")

    def _obj(number: int, body: bytes) -> bytes:
        return f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    parts = [
        _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _obj(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        _obj(4, f"<< /Length {len(payload)} >>\nstream\n".encode() + payload + b"\nendstream"),
        _obj(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]
    body = b"%PDF-1.4\n"
    offsets = [0]
    for part in parts:
        offsets.append(len(body))
        body += part
    xref = b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets[1:])
    return body + xref + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(len(body)).encode() + b"\n%%EOF\n"

def _text_png(text: str = "INVOICE 42") -> bytes:
    image = Image.new("RGB", (400, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 20), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_extract_pdf_text_returns_embedded_words():
    data = _minimal_pdf("Service Level Agreement Draft")
    result = extract_pdf_text(data)
    assert result["status"] == "ok"
    assert result["engine"] == "PyPDF2"
    assert "Service Level Agreement Draft" in result["text"]


def test_extract_image_ocr_when_tesseract_available():
    data = _text_png("INVOICE 42")
    result = extract_attachment_bytes(data, mime_type="image/png", filename="invoice.png")
    assert result["mime_type"] == "image/png"
    if result["status"] == "skipped" and "Tesseract" in (result.get("reason") or ""):
        pytest.skip("Tesseract is not installed")
    if result["status"] == "ok":
        assert "INVOICE" in result["text"].upper() or "42" in result["text"]


def test_gmail_attachment_id_matching_and_filename_fallback():
    long_id = "ANGjdJ-" + ("x" * 80)
    items = [
        {"attachment_id": long_id, "filename": "Crystal_Logic.pdf", "mime_type": "application/pdf"},
        {"attachment_id": "cid-part", "content_id": "ii_logo", "filename": "inline-ii_logo", "mime_type": "image/png"},
    ]
    assert gw._gmail_attachment_ids_equal(long_id, long_id[:40])
    matched = gw._resolve_gmail_attachment_part(items, attachment_id=long_id[:40])
    assert matched["filename"] == "Crystal_Logic.pdf"
    by_name = gw._resolve_gmail_attachment_part(items, attachment_id="missing", filename_hint="Crystal_Logic.pdf")
    assert by_name["attachment_id"] == long_id
    by_cid = gw._resolve_gmail_attachment_part(items, content_id="ii_logo")
    assert by_cid["attachment_id"] == "cid-part"


def test_parse_email_includes_parts_without_filename():
    result = {
        "threadId": "t1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Files"}],
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "",
                    "body": {"attachmentId": "att-no-name", "size": 12},
                    "headers": [],
                },
                {
                    "mimeType": "image/png",
                    "filename": "",
                    "body": {"attachmentId": "att-inline", "size": 9},
                    "headers": [{"name": "Content-ID", "value": "<logo@mail>"}],
                },
            ],
        },
        "snippet": "",
        "labelIds": [],
    }
    parsed = gw.GoogleWorkspaceConnector._parse_email(result, "msg-1")
    ids = {row["attachment_id"]: row for row in parsed["attachments"]}
    assert "att-no-name" in ids
    assert ids["att-inline"]["inline"] is True
    assert ids["att-inline"]["content_id"] == "logo@mail"


def test_fetch_email_attachment_bytes_uses_part_filename(monkeypatch):
    connector = gw.GoogleWorkspaceConnector.__new__(gw.GoogleWorkspaceConnector)
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)
    long_id = "ANGjdJ-" + ("y" * 80)
    monkeypatch.setattr(connector, "get_email", lambda message_id: {
        "attachments": [{"attachment_id": long_id, "filename": "brief.pdf", "mime_type": "application/pdf"}],
    })
    captured = {}

    def fake_request(method, url):
        captured["url"] = url
        return {"data": base64.urlsafe_b64encode(b"%PDF-1.4 sample").decode()}

    monkeypatch.setattr(connector, "_make_request", fake_request)
    data, mime, name = connector.fetch_email_attachment_bytes("mid", long_id[:30], filename_hint="ignored.pdf")
    assert name == "brief.pdf"
    assert mime == "application/pdf"
    assert data.startswith(b"%PDF")
    assert long_id in captured["url"]


def test_triage_prompt_includes_attachment_names_and_text():
    from distr.core.incoming.proposals import _triage_prompt

    conversation = {
        "source": "gmail",
        "participant": "Paul",
        "label": "Proposed support agreement",
        "items": [{
            "source_message_id": "msg-1",
            "created_at": "2026-11-11T13:06:18+00:00",
            "sender": "Paul",
            "text": "Please review the draft.",
            "attachments": [{
                "filename": "Crystal_Logic.pdf",
                "mime_type": "application/pdf",
                "size": 91370,
                "extracted_text": "Tensology will provide ongoing support.",
            }],
        }],
    }
    messages = _triage_prompt(conversation, [{"id": 1, "name": "Crystal Logic"}])
    payload = json.loads(messages[1]["content"])
    attachments = payload["messages"][0]["attachments"]
    assert attachments[0]["name"] == "Crystal_Logic.pdf"
    assert attachments[0]["mime_type"] == "application/pdf"
    assert "ongoing support" in attachments[0]["extracted_text"]
    assert "extracted text" in messages[0]["content"].lower() or "PDF text" in messages[0]["content"]


def test_proposal_from_triage_copies_mail_attachments():
    from distr.core.incoming.proposals import _proposal_from_triage

    conversation = {
        "source": "gmail",
        "thread_id": "thread-9",
        "participant": "Paul",
        "label": "Agreement",
        "items": [{
            "source_message_id": "msg-9",
            "created_at": "2026-11-11T13:06:18+00:00",
            "sender": "Paul",
            "text": "See attached.",
            "direction": "inbound",
            "attachments": [{
                "filename": "scope.pdf",
                "attachment_id": "att-9",
                "message_id": "msg-9",
                "mime_type": "application/pdf",
                "extracted_text": "Scope of work",
                "path": "/tmp/scope.pdf",
            }],
        }],
    }
    proposal = _proposal_from_triage(conversation, {"id": 3, "name": "Crystal Logic"}, {
        "title": "Review agreement", "description": "Review the attached draft", "is_actionable": True,
    })
    assert proposal["attachments"]
    assert proposal["attachments"][0]["name"] == "scope.pdf"
    assert "Scope of work" in proposal["attachments"][0]["extracted_text"]


def test_store_mail_attachment_writes_bytes_and_record_json(tmp_path, monkeypatch):
    from distr.core.incoming import media as media_mod
    from distr.core.incoming import mail_attachments as mail_mod

    monkeypatch.setattr(media_mod, "_root", lambda project_id, reference: tmp_path / "incoming")
    # store_mail_attachment imports _root at call time from .media
    monkeypatch.setattr("distr.core.incoming.media._root", lambda project_id, reference: tmp_path / "incoming")

    pdf = _minimal_pdf("Stored agreement text")

    def fake_download(item):
        return pdf, "application/pdf", "agreement.pdf"

    record = store_mail_attachment(
        {"name": "agreement.pdf", "message_id": "m1", "attachment_id": "a1", "provider": "gmail"},
        project_id=None,
        reference="local:gmail:t1",
        downloader=fake_download,
    )
    path = Path(record["path"])
    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF")
    sidecar = path.with_name(path.name + ".record.json")
    assert sidecar.is_file()
    saved = json.loads(sidecar.read_text())
    assert saved["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert saved["id"] == record["id"]
    assert record.get("extraction_status") in {"ok", "empty", "error", "skipped"}


def test_enrich_conversation_then_thread_context(tmp_path, monkeypatch):
    monkeypatch.setattr("distr.core.incoming.media._root", lambda project_id, reference: tmp_path / "incoming")
    png = _text_png("SITE PHOTO")
    pdf = _minimal_pdf("Payment terms")

    def fake_download(item):
        if item["name"].endswith(".png"):
            return png, "image/png", item["name"]
        return pdf, "application/pdf", item["name"]

    conversation = {
        "source": "mailshot",
        "thread_id": "email-abc",
        "project_id": None,
        "items": [{
            "source_message_id": "email-abc",
            "attachments": [
                {"filename": "terms.pdf", "attachment_id": "p1", "message_id": "email-abc", "mime_type": "application/pdf", "provider": "mailshot"},
                {"filename": "photo.png", "attachment_id": "p2", "message_id": "email-abc", "mime_type": "image/png", "provider": "mailshot"},
            ],
        }],
    }
    stored = enrich_conversation_mail_attachments(conversation, downloader=fake_download)
    assert len(stored) == 2
    assert all(Path(row["path"]).is_file() for row in stored)
    prompt_rows = prompt_attachments_for_item(conversation["items"][0])
    assert {row["name"] for row in prompt_rows} == {"terms.pdf", "photo.png"}

    from distr.core.workflow import development_harness as harness

    # Build the same prompt fragment the CLI harness uses.
    metadata = {"turn_attachments": harness._safe_turn_attachments(stored, project_folder=str(tmp_path))}
    # _safe_turn_attachments only accepts files under ~/.decisions/workspaces/projects or project_folder.
    # Re-copy into project_folder so the harness includes them.
    copied = []
    for row in stored:
        dest = tmp_path / Path(row["path"]).name
        dest.write_bytes(Path(row["path"]).read_bytes())
        copied.append({**row, "path": str(dest)})
    safe = harness._safe_turn_attachments(copied, project_folder=str(tmp_path))
    assert len(safe) == 2
    rendered = "\n".join(
        f"- {item.get('name')}: {item.get('path')}"
        + (f"\n  extracted_text: {item.get('extracted_text') or item.get('transcript') or ''}" if item.get("extracted_text") or item.get("transcript") else "")
        for item in safe
    )
    assert "terms.pdf" in rendered
    assert str(tmp_path) in rendered


def test_developer_context_lists_attachment_paths_and_excerpts():
    from distr.core.developer_context import DeveloperRuntimeContext, DeveloperWorkContext, format_developer_context_dict_for_prompt

    context = {
        "runtime": {"cwd": "/tmp", "current_chat_id": 42, "debug_mode": False},
        "active_thread": {
            "chat_id": 42,
            "attachments": [{
                "name": "Crystal_Logic.pdf",
                "path": "/tmp/Crystal_Logic.pdf",
                "mime_type": "application/pdf",
                "extracted_text": "Tensology will provide ongoing support.",
            }],
        },
    }
    text = format_developer_context_dict_for_prompt(context, max_chars=4000)
    assert "Crystal_Logic.pdf" in text
    assert "/tmp/Crystal_Logic.pdf" in text
    assert "ongoing support" in text

    work = DeveloperWorkContext(
        runtime=DeveloperRuntimeContext(cwd="/tmp", current_chat_id=42),
        active_thread=context["active_thread"],
    )
    prompt = work.to_prompt_text(max_chars=4000)
    assert "Crystal_Logic.pdf" in prompt
    assert "ongoing support" in prompt


def test_ingest_gmail_as_work_passes_attachments(monkeypatch):
    captured = {}

    class FakeService:
        def ingest(self, intake):
            captured["intake"] = intake
            return SimpleNamespace(to_dict=lambda: {"action": "create_ticket", "attachment_count": len(intake.attachments)})

    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service", lambda: FakeService())
    from distr.core.incoming.service import ingest_gmail_as_work

    result = ingest_gmail_as_work(
        subject="Agreement",
        body="Please review",
        message_id="m1",
        attachments=[{"filename": "brief.pdf", "mime_type": "application/pdf", "path": "/tmp/brief.pdf"}],
    )
    assert captured["intake"].attachments[0].name == "brief.pdf"
    assert captured["intake"].attachments[0].mime_type == "application/pdf"
    assert result["attachment_count"] == 1


def test_work_scanner_hydrates_mailshot_and_includes_attachments(monkeypatch):
    from distr.core.initiative import work_scanner

    seen = {}

    def fake_mailshot(**kwargs):
        seen["kwargs"] = kwargs
        return [{
            "id": "email-1",
            "thread_id": "email-1",
            "from": "Client <client@example.com>",
            "subject": "Please review the invoice",
            "snippet": "Invoice attached",
            "body": "Invoice attached",
            "source": "mailshot",
            "attachments": [{"filename": "I4208W.pdf", "mime_type": "application/pdf", "attachment_id": "att-1", "extracted_text": "Amount due 1200"}],
        }]

    monkeypatch.setattr("distr.core.kanban.jira_intake.fetch_mailshot_intake_messages", fake_mailshot)
    monkeypatch.setattr("distr.core.kanban.jira_intake.fetch_gmail_intake_messages", lambda **kwargs: [])
    monkeypatch.setattr(
        "distr.core.kanban.email_intake_rules.resolve_email_link",
        lambda sender: SimpleNamespace(board_id=7, project_id=9, board_name="Board", project_name="Project", sender_email="client@example.com"),
    )
    scan = {"messages": {"email": []}, "proposals": [], "unavailable_sources": []}
    work_scanner._scan_email_uncached(scan)
    assert seen["kwargs"].get("hydrate") is True
    assert scan["messages"]["email"][0]["attachments"][0]["name"] == "I4208W.pdf"
    assert "Amount due 1200" in scan["messages"]["email"][0]["attachments"][0]["extracted_text"]


def test_workspace_tool_downloads_mailshot_attachment(tmp_path, monkeypatch):
    from distr.core.agent.tools.integrations.tensology_workspace import TensologyWorkspaceTool

    monkeypatch.setattr(
        "distr.core.incoming.mailshot_media.fetch_mailshot_attachment_bytes",
        lambda message_id, attachment_id, filename_hint="": (b"%PDF-1.4 x", "application/pdf", filename_hint or "file.pdf"),
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        lambda source="": SimpleNamespace(),
    )
    tool = TensologyWorkspaceTool()
    raw = tool._run(
        "download_mail_attachment",
        {"message_id": "email-1", "attachment_id": "att-1", "filename": "file.pdf", "destination_dir": str(tmp_path)},
    )
    result = json.loads(raw) if raw.strip().startswith("{") else raw
    if isinstance(result, str):
        # Tool may wrap as JSON string or already decoded.
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            pytest.fail(raw)
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).read_bytes().startswith(b"%PDF")
    assert result["filename"] == "file.pdf"

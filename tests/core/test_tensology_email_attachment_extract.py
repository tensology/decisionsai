"""Standalone Tensology attachment extract + Gmail part parsing tests (no Django, no DB)."""
from __future__ import annotations

import ast
import importlib.util
import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

TENSOLOGY_MAIN = Path(
    "/Users/paul/development/TENSOLOGY/DONE/www.tensology.com/backend/webapp/apps/main"
)


def _load_extract_module():
    path = TENSOLOGY_MAIN / "incoming_attachment_text.py"
    spec = importlib.util.spec_from_file_location("tensology_incoming_attachment_text", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gmail_attachments_fn():
    """Load GmailSource._attachments without importing the Django-backed module."""
    source = (TENSOLOGY_MAIN / "incoming_sources.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "GmailSource":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_attachments":
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    ns: dict = {}
                    exec(compile(module, "incoming_sources.py", "exec"), ns)

                    class GmailSource:
                        _attachments = classmethod(ns["_attachments"])

                    return GmailSource._attachments
    raise AssertionError("GmailSource._attachments was not found")


def _minimal_pdf(text: str = "Mailshot invoice 1200") -> bytes:
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
    ImageDraw.Draw(image).text((10, 20), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_tensology_extract_pdf_and_image():
    extract = _load_extract_module()
    pdf_result = extract.extract_attachment_bytes(
        _minimal_pdf("Service Level Agreement Draft"),
        mime_type="application/pdf",
        filename="sla.pdf",
    )
    assert pdf_result["mime_type"] == "application/pdf"
    assert pdf_result["status"] == "ok"
    assert "Service Level Agreement Draft" in pdf_result["text"]

    image_result = extract.extract_attachment_bytes(
        _text_png("INVOICE 42"),
        mime_type="image/png",
        filename="invoice.png",
    )
    if image_result["status"] == "skipped" and "Tesseract" in (image_result.get("reason") or ""):
        pytest.skip("Tesseract is not installed")
    if image_result["status"] == "ok":
        assert "INVOICE" in image_result["text"].upper() or "42" in image_result["text"]


def test_tensology_gmail_includes_inline_parts_without_filenames():
    _attachments = _gmail_attachments_fn()
    payload = {
        "mimeType": "multipart/mixed",
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
            {
                "mimeType": "application/pdf",
                "filename": "I4208W.pdf",
                "body": {"attachmentId": "att-named", "size": 100},
                "headers": [],
            },
        ],
    }
    rows = _attachments(payload)
    by_id = {row["attachment_id"]: row for row in rows}
    assert by_id["att-no-name"]["filename"] == "attachment"
    assert by_id["att-inline"]["inline"] is True
    assert by_id["att-inline"]["content_id"] == "logo@mail"
    assert by_id["att-inline"]["filename"] == "inline-logo@mail"
    assert by_id["att-named"]["filename"] == "I4208W.pdf"

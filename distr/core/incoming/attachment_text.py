"""Extract text from stored mail attachments for triage and thread context.

Defaults (2026-11-11 email-attachment pipeline):
- PDFs: PyPDF2, at most 20 pages and 20_000 characters. Encrypted/empty PDFs are skipped.
- Images: local Tesseract OCR via pytesseract when the binary is present, at most 8 MiB,
  30s timeout. No cloud vision provider is called.
- Other types are recorded as skipped so the prompt still lists name, mime, and size.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PDF_PAGE_CAP = 20
EXTRACT_CHAR_CAP = 20_000
PDF_BYTE_CAP = 25 * 1024 * 1024
IMAGE_BYTE_CAP = 8 * 1024 * 1024
OCR_TIMEOUT_SECONDS = 30
EXCERPT_CHAR_CAP = 1500

_IMAGE_MIME = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/tiff", "image/bmp",
}
_PDF_MIME = {"application/pdf"}


def _guess_mime(filename: str, mime: str = "") -> str:
    raw = str(mime or "").split(";", 1)[0].strip().lower()
    if raw and raw != "application/octet-stream":
        return raw
    guessed = (mimetypes.guess_type(filename or "")[0] or "").lower()
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    return guessed or raw or "application/octet-stream"


def clip_extracted_text(text: str, limit: int = EXTRACT_CHAR_CAP) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def excerpt_extracted_text(text: str, limit: int = EXCERPT_CHAR_CAP) -> str:
    return clip_extracted_text(text, limit=limit)


def extract_pdf_text(data: bytes) -> dict[str, Any]:
    if not data:
        return {"text": "", "status": "empty", "reason": "PDF was empty.", "engine": ""}
    if len(data) > PDF_BYTE_CAP:
        return {"text": "", "status": "skipped", "reason": f"PDF exceeds {PDF_BYTE_CAP} byte cap.", "engine": ""}
    if not data.startswith(b"%PDF"):
        return {"text": "", "status": "skipped", "reason": "Bytes are not a PDF.", "engine": ""}
    try:
        import io
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                return {"text": "", "status": "skipped", "reason": "PDF is encrypted.", "engine": "PyPDF2"}
        pages = list(reader.pages)[:PDF_PAGE_CAP]
        chunks: list[str] = []
        for page in pages:
            try:
                chunks.append(str(page.extract_text() or ""))
            except Exception:
                continue
        text = clip_extracted_text("\n".join(chunks))
        return {
            "text": text,
            "status": "ok" if text else "empty",
            "reason": "" if text else "PDF had no extractable text.",
            "engine": "PyPDF2",
            "pages": len(pages),
        }
    except Exception as exc:
        logger.info("PDF text extraction failed", exc_info=True)
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": "PyPDF2"}


def extract_image_text(data: bytes, mime: str = "") -> dict[str, Any]:
    if not data:
        return {"text": "", "status": "empty", "reason": "Image was empty.", "engine": ""}
    if len(data) > IMAGE_BYTE_CAP:
        return {"text": "", "status": "skipped", "reason": f"Image exceeds {IMAGE_BYTE_CAP} byte cap.", "engine": ""}
    try:
        import io
        import shutil

        import pytesseract
        from PIL import Image

        if not shutil.which("tesseract"):
            return {"text": "", "status": "skipped", "reason": "Tesseract is not installed.", "engine": ""}
        image = Image.open(io.BytesIO(data))
        if image.mode not in {"L", "RGB"}:
            image = image.convert("RGB")
        # Bound extreme dimensions so OCR cannot hang the triage thread.
        image.thumbnail((4000, 4000))
        text = clip_extracted_text(pytesseract.image_to_string(image, timeout=OCR_TIMEOUT_SECONDS))
        return {
            "text": text,
            "status": "ok" if text else "empty",
            "reason": "" if text else "OCR found no text.",
            "engine": "tesseract",
        }
    except Exception as exc:
        logger.info("Image OCR failed", exc_info=True)
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": "tesseract"}


def extract_attachment_bytes(data: bytes, *, mime_type: str = "", filename: str = "") -> dict[str, Any]:
    """Return extracted text plus status for one attachment's bytes."""
    mime = _guess_mime(filename, mime_type)
    suffix = Path(filename or "").suffix.lower()
    if mime in _PDF_MIME or suffix == ".pdf" or (data or b"").startswith(b"%PDF"):
        result = extract_pdf_text(data or b"")
    elif mime in _IMAGE_MIME or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}:
        result = extract_image_text(data or b"", mime)
    else:
        result = {
            "text": "",
            "status": "skipped",
            "reason": f"No text extractor for {mime or 'unknown type'}.",
            "engine": "",
        }
    result["mime_type"] = mime
    result["filename"] = Path(filename or "attachment").name
    return result


def extract_attachment_file(path: str | Path, *, mime_type: str = "", filename: str = "") -> dict[str, Any]:
    location = Path(path)
    name = filename or location.name
    try:
        data = location.read_bytes()
    except OSError as exc:
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": "", "mime_type": mime_type, "filename": name}
    return extract_attachment_bytes(data, mime_type=mime_type, filename=name)


def apply_extraction(record: dict[str, Any], extracted: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write extraction fields onto a media record (does not rewrite bytes)."""
    result = dict(record)
    payload = extracted
    if payload is None and result.get("path"):
        payload = extract_attachment_file(result["path"], mime_type=str(result.get("mime_type") or ""), filename=str(result.get("name") or ""))
    payload = payload or {}
    text = clip_extracted_text(str(payload.get("text") or result.get("extracted_text") or result.get("transcript") or ""))
    if text:
        result["extracted_text"] = text
        result.setdefault("transcript", text)
    result["extraction_status"] = str(payload.get("status") or result.get("extraction_status") or "")
    if payload.get("reason"):
        result["extraction_reason"] = str(payload["reason"])[:300]
    if payload.get("engine"):
        result["extraction_engine"] = str(payload["engine"])
    return result

"""Extract text locally from stored attachments for triage and thread context."""
from __future__ import annotations

import importlib
import io
import logging
import mimetypes
import shutil
import subprocess
import tempfile
from itertools import islice
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PDF_PAGE_CAP = 20
OCR_PDF_PAGE_CAP = 10
EXTRACT_CHAR_CAP = 20_000
PDF_BYTE_CAP = 25 * 1024 * 1024
IMAGE_BYTE_CAP = 8 * 1024 * 1024
OCR_TIMEOUT_SECONDS = 30
EXCERPT_CHAR_CAP = 1500

_IMAGE_MIME = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/tiff", "image/bmp",
}
_PDF_MIME = {"application/pdf"}
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_HEIF_MIME = {"image/heic", "image/heif"}


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


def _optional_module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _missing_dependencies(reason: str, missing: list[str]) -> dict[str, Any]:
    return {"text": "", "status": "missing_dependency", "reason": reason, "engine": "",
            "missing_dependencies": missing}


def _ocr_dependencies():
    missing = []
    if not shutil.which("tesseract"):
        missing.append("tesseract")
    pytesseract = _optional_module("pytesseract")
    if pytesseract is None:
        missing.append("pytesseract")
    return pytesseract, missing


def _ocr_pdf(data: bytes, pages: int) -> dict[str, Any]:
    engine = "pdftoppm+tesseract"
    pdftoppm = shutil.which("pdftoppm")
    pytesseract, missing = _ocr_dependencies()
    if not pdftoppm:
        missing.insert(0, "pdftoppm")
    if missing:
        return {**_missing_dependencies(f"PDF has no text layer; OCR needs {', '.join(missing)}.", missing),
                "pages": pages}

    count = min(pages, OCR_PDF_PAGE_CAP)
    chunks = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            prefix = Path(directory) / "page"
            source.write_bytes(data)
            subprocess.run(
                [pdftoppm, "-r", "200", "-png", "-f", "1", "-l", str(count), str(source), str(prefix)],
                check=True, capture_output=True, timeout=60,
            )
            rendered = sorted(prefix.parent.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
            failed = count - len(rendered)
            for page in rendered:
                try:
                    chunks.append(pytesseract.image_to_string(str(page), timeout=OCR_TIMEOUT_SECONDS))
                except Exception:
                    failed += 1
    except Exception as exc:
        logger.info("PDF OCR failed", exc_info=True)
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": engine, "pages": pages}

    text = clip_extracted_text("\n".join(chunks))
    if failed:
        status = "partial" if failed < count else "empty"
        reason = f"{failed} of {count} pages could not be read."
    else:
        status = "ok" if text else "empty"
        reason = "" if text else "OCR found no text."
    return {"text": text, "status": status, "reason": reason, "engine": engine, "pages": pages}


def extract_pdf_text(data: bytes) -> dict[str, Any]:
    if not data:
        return {"text": "", "status": "empty", "reason": "PDF was empty.", "engine": ""}
    if len(data) > PDF_BYTE_CAP:
        return {"text": "", "status": "skipped", "reason": f"PDF exceeds {PDF_BYTE_CAP} byte cap.", "engine": ""}
    if not data.startswith(b"%PDF"):
        return {"text": "", "status": "skipped", "reason": "Bytes are not a PDF.", "engine": ""}
    try:
        pdf = _optional_module("PyPDF2")
        if pdf is None:
            return _missing_dependencies("Missing dependency: PyPDF2 is needed to read PDFs.", ["PyPDF2"])
        reader = pdf.PdfReader(io.BytesIO(data), strict=False)
        encrypted = reader.is_encrypted
        if encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception:
                decrypted = 0
            if not decrypted:
                return {"text": "", "status": "skipped", "reason": "PDF is encrypted.", "engine": "PyPDF2"}
        pages = list(islice(reader.pages, PDF_PAGE_CAP))
        chunks: list[str] = []
        failed = 0
        for page in pages:
            try:
                chunks.append(str(page.extract_text() or ""))
            except Exception:
                failed += 1
        text = clip_extracted_text("\n".join(chunks))
        if not text and pages and not encrypted:
            return _ocr_pdf(data, len(pages))
        return {
            "text": text,
            "status": ("partial" if failed else "ok") if text else "empty",
            "reason": (f"{failed} of {len(pages)} pages could not be read." if failed else "")
            if text else "PDF had no extractable text.",
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
        pytesseract, missing = _ocr_dependencies()
        if missing:
            return _missing_dependencies(f"Image OCR needs {', '.join(missing)}.", missing)
        Image = _optional_module("PIL.Image")
        if Image is None:
            return _missing_dependencies("Image OCR needs Pillow.", ["Pillow"])
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


def _extract_word_text(data: bytes, *, docx: bool) -> dict[str, Any]:
    if not data:
        return {"text": "", "status": "empty", "reason": "Word document was empty.", "engine": ""}
    if len(data) > PDF_BYTE_CAP:
        return {"text": "", "status": "skipped", "reason": f"Word document exceeds {PDF_BYTE_CAP} byte cap.", "engine": ""}
    engine = "python-docx" if docx else ""
    try:
        if docx:
            document_module = _optional_module("docx")
            if document_module is None:
                return {"text": "", "status": "error", "reason": "Missing dependency: python-docx.", "engine": engine}
            document = document_module.Document(io.BytesIO(data))
            chunks = [paragraph.text for paragraph in document.paragraphs]
            chunks.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
            text = clip_extracted_text("\n".join(chunks))
        else:
            engine = "textutil"
            executable = shutil.which(engine)
            options = ["-convert", "txt", "-stdout"]
            if not executable:
                engine = "antiword"
                executable = shutil.which(engine)
                options = []
            if not executable:
                return _missing_dependencies(
                    "Missing dependency: textutil or antiword is needed to read .doc files.", ["textutil", "antiword"],
                )
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.doc"
                path.write_bytes(data)
                completed = subprocess.run(
                    [executable, *options, str(path)], check=True, capture_output=True, timeout=30,
                )
                text = clip_extracted_text(completed.stdout.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.info("Word text extraction failed", exc_info=True)
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": engine}
    return {"text": text, "status": "ok" if text else "empty",
            "reason": "" if text else "Word document had no extractable text.", "engine": engine}


def _extract_heif_text(data: bytes, mime: str, suffix: str) -> dict[str, Any]:
    if not data or len(data) > IMAGE_BYTE_CAP:
        return extract_image_text(data, mime)
    try:
        pillow_heif = _optional_module("pillow_heif")
        if pillow_heif is not None:
            pillow_heif.register_heif_opener()
            return extract_image_text(data, mime)
        sips = shutil.which("sips")
        if not sips:
            return _missing_dependencies(
                "Missing dependency: pillow-heif or sips is needed to read HEIC/HEIF images.", ["pillow-heif", "sips"],
            )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / ("source.heif" if suffix == ".heif" or mime == "image/heif" else "source.heic")
            jpeg = source.with_suffix(".jpg")
            source.write_bytes(data)
            subprocess.run(
                [sips, "-s", "format", "jpeg", str(source), "--out", str(jpeg)],
                check=True, capture_output=True, timeout=30,
            )
            with jpeg.open("rb") as converted:
                return extract_image_text(converted.read(IMAGE_BYTE_CAP + 1), "image/jpeg")
    except Exception as exc:
        logger.info("HEIC/HEIF conversion failed", exc_info=True)
        return {"text": "", "status": "error", "reason": str(exc)[:300], "engine": ""}


def extract_attachment_bytes(data: bytes, *, mime_type: str = "", filename: str = "") -> dict[str, Any]:
    """Return extracted text plus status for one attachment's bytes."""
    mime = _guess_mime(filename, mime_type)
    suffix = Path(filename or "").suffix.lower()
    if mime in _PDF_MIME or suffix == ".pdf" or (data or b"").startswith(b"%PDF"):
        result = extract_pdf_text(data or b"")
    elif mime == _DOCX_MIME or suffix == ".docx":
        result = _extract_word_text(data or b"", docx=True)
    elif mime == "application/msword" or suffix == ".doc":
        result = _extract_word_text(data or b"", docx=False)
    elif mime in _HEIF_MIME or suffix in {".heic", ".heif"}:
        result = _extract_heif_text(data or b"", mime, suffix)
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
        with location.open("rb") as source:
            data = source.read(max(PDF_BYTE_CAP, IMAGE_BYTE_CAP) + 1)
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
    if "missing_dependencies" in payload:
        result["missing_dependencies"] = list(payload["missing_dependencies"])
    return result

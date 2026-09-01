"""Shared strict file validation for BuildWise Pro web and Telegram workflows."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile


MAX_TEXT_TEMPLATE_BYTES = 256 * 1024
MAX_TEXT_SOURCE_BYTES = 256 * 1024
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ALLOWED_DRAWING_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".txt"}
_ALLOWED_TEMPLATE_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}


class ProFileValidationError(ValueError):
    """Raised when a professional upload fails strict type/content validation."""


def _validate_docx(data: bytes, *, label: str) -> str:
    if not data.startswith(b"PK"):
        raise ProFileValidationError(f"{label} must be a valid DOCX file.")
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except BadZipFile as exc:
        raise ProFileValidationError(f"{label} must be a valid DOCX file.") from exc
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ProFileValidationError(f"{label} must be a valid DOCX file.")
    return _DOCX_MIME


def _validate_utf8_text(data: bytes, *, label: str, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise ProFileValidationError(f"{label} is too large.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProFileValidationError(f"{label} must use UTF-8 text.") from exc
    return "text/plain"


def detect_pro_file_type(filename: str, data: bytes, *, template: bool = False) -> str:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or not suffix:
        raise ProFileValidationError("A supported filename and extension are required.")
    if not data:
        raise ProFileValidationError("Uploaded file is empty.")

    if template:
        if suffix not in _ALLOWED_TEMPLATE_EXTENSIONS:
            raise ProFileValidationError(
                "Company template must be PDF, DOCX, TXT, Markdown (.md), or .markdown."
            )
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ProFileValidationError("Company template must be a valid PDF.")
            return "application/pdf"
        if suffix == ".docx":
            return _validate_docx(data, label="Company template")
        if suffix in {".md", ".markdown"}:
            _validate_utf8_text(
                data, label="Text/Markdown company template", max_bytes=MAX_TEXT_TEMPLATE_BYTES
            )
            return "text/markdown"
        return _validate_utf8_text(
            data, label="Text company template", max_bytes=MAX_TEXT_TEMPLATE_BYTES
        )

    if suffix not in _ALLOWED_DRAWING_EXTENSIONS:
        raise ProFileValidationError(
            "Project source must be PDF, DOCX, TXT, PNG, JPG, or JPEG."
        )
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".docx":
        return _validate_docx(data, label="Project source")
    if suffix == ".txt":
        return _validate_utf8_text(
            data, label="Project source text", max_bytes=MAX_TEXT_SOURCE_BYTES
        )
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ProFileValidationError(
        "File content does not match the permitted BuildWise Pro source type."
    )

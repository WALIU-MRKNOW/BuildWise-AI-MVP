"""Shared strict file validation for BuildWise Pro web and Telegram workflows."""

from __future__ import annotations

from pathlib import Path


MAX_TEXT_TEMPLATE_BYTES = 256 * 1024
_ALLOWED_DRAWING_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
_ALLOWED_TEMPLATE_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}


class ProFileValidationError(ValueError):
    """Raised when a professional upload fails strict type/content validation."""


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
                "Company template must be PDF, TXT, Markdown (.md), or .markdown."
            )
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ProFileValidationError("Company template must be a valid PDF.")
            return "application/pdf"
        if len(data) > MAX_TEXT_TEMPLATE_BYTES:
            raise ProFileValidationError("Text/Markdown company template is too large.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProFileValidationError(
                "Text/Markdown company template must use UTF-8 text."
            ) from exc
        return "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"

    if suffix not in _ALLOWED_DRAWING_EXTENSIONS:
        raise ProFileValidationError("Drawing must be PDF, PNG, JPG, or JPEG.")
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ProFileValidationError(
        "File content does not match the permitted drawing/sketch type."
    )

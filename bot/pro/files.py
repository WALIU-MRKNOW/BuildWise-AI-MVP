"""Shared strict file validation for BuildWise Pro web and Telegram workflows."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo


MAX_TEXT_TEMPLATE_BYTES = 256 * 1024
MAX_TEXT_SOURCE_BYTES = 256 * 1024
MAX_DOCX_MEMBERS = 256
MAX_DOCX_EXPANDED_BYTES = 48 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ALLOWED_DRAWING_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".txt"}
_ALLOWED_TEMPLATE_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}


class ProFileValidationError(ValueError):
    """Raised when a professional upload fails strict type/content validation."""


def _safe_docx_member(info: ZipInfo) -> bool:
    """Reject suspicious archive members before a DOCX parser expands them."""
    path = PurePosixPath(info.filename)
    if path.is_absolute() or ".." in path.parts:
        return False
    if info.flag_bits & 0x1:  # encrypted ZIP member
        return False
    return True


def _validate_docx(data: bytes, *, label: str) -> str:
    if not data.startswith(b"PK"):
        raise ProFileValidationError(f"{label} must be a valid DOCX file.")
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise ProFileValidationError(f"{label} contains too many embedded files.")

            expanded_total = 0
            names: set[str] = set()
            for info in members:
                if not _safe_docx_member(info):
                    raise ProFileValidationError(f"{label} contains an unsafe archive entry.")
                names.add(info.filename)
                if info.is_dir():
                    continue

                if info.file_size < 0 or info.compress_size < 0:
                    raise ProFileValidationError(f"{label} contains an invalid archive entry.")
                if info.file_size > MAX_DOCX_MEMBER_BYTES:
                    raise ProFileValidationError(f"{label} expands beyond the permitted size.")

                expanded_total += info.file_size
                if expanded_total > MAX_DOCX_EXPANDED_BYTES:
                    raise ProFileValidationError(f"{label} expands beyond the permitted size.")

                if info.file_size > 1024 * 1024:
                    compressed = max(info.compress_size, 1)
                    if info.file_size / compressed > MAX_DOCX_COMPRESSION_RATIO:
                        raise ProFileValidationError(
                            f"{label} uses an unsafe compression ratio."
                        )
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

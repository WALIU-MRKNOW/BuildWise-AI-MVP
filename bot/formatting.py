"""Concise, Telegram-safe BuildWise response formatting."""

from __future__ import annotations

import re

from bot.domain import Mode
from bot.study_experience import StudyRequestKind, strip_learning_marker


TELEGRAM_SAFE_CHUNK = 3900

MODE_HEADERS = {
    Mode.STUDY: "📚 BUILDWISE · STUDY & SOLVE",
    Mode.SKETCH: "📐 BUILDWISE · SKETCH ANALYSIS",
    Mode.BUILDING: "🏗️ BUILDWISE · BUILDING ANALYSIS",
}

STUDY_HEADERS = {
    StudyRequestKind.CALCULATION: "🧮 BUILDWISE · SOLVING CALCULATION",
    StudyRequestKind.TOPIC: "📚 BUILDWISE · EXPLAINING TOPIC",
}

DISCLAIMERS = {
    Mode.BUILDING: (
        "Safety note: This is a preliminary photo review, not an on-site inspection "
        "or a declaration of structural safety. Use a qualified local professional."
    ),
    Mode.SKETCH: (
        "Review note: This is not construction, code, structural, or permit approval. "
        "Confirm the design with qualified local professionals."
    ),
}


def clean_student_math(text: str) -> str:
    """Turn common model LaTeX into readable Telegram text.

    The model is instructed not to emit LaTeX, but this sanitizer prevents raw
    commands from reaching students when a model response still contains them.
    """

    cleaned = text
    cleaned = re.sub(r"\\text\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\mathbf\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", cleaned)
    replacements = {
        r"\times": "×",
        r"\div": "÷",
        r"\cdot": "·",
        r"\approx": "≈",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\le": "≤",
        r"\ge": "≥",
        r"\sqrt": "√",
        r"\%": "%",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = cleaned.replace(r"\[", "").replace(r"\]", "")
    cleaned = cleaned.replace(r"\(", "").replace(r"\)", "")
    cleaned = cleaned.replace("$$", "").replace("$", "")
    cleaned = re.sub(r"\^\{2\}", "²", cleaned)
    cleaned = re.sub(r"\^\{3\}", "³", cleaned)
    cleaned = re.sub(r"\^2\b", "²", cleaned)
    cleaned = re.sub(r"\^3\b", "³", cleaned)
    cleaned = re.sub(r"\\(?:begin|end)\{[^{}]+\}", "", cleaned)
    cleaned = re.sub(r"\\[A-Za-z]+", "", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_response(mode: Mode, model_text: str) -> str:
    kind = None
    cleaned = model_text.strip()
    if mode is Mode.STUDY:
        kind, cleaned = strip_learning_marker(cleaned)
        cleaned = clean_student_math(cleaned)
    else:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned:
        cleaned = "I could not produce a useful answer. Please try a clearer request."
    header = STUDY_HEADERS.get(kind, MODE_HEADERS[mode]) if mode is Mode.STUDY else MODE_HEADERS[mode]
    parts = [header, cleaned]
    if mode in DISCLAIMERS:
        parts.append(DISCLAIMERS[mode])
    return "\n\n".join(parts)


def split_for_telegram(text: str, limit: int = TELEGRAM_SAFE_CHUNK) -> list[str]:
    if limit < 50:
        raise ValueError("Chunk limit is too small")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[: limit + 1]
        split_at = window.rfind("\n\n", 0, limit + 1)
        if split_at < limit // 3:
            split_at = window.rfind("\n", 0, limit + 1)
        if split_at < limit // 3:
            split_at = window.rfind(" ", 0, limit + 1)
        if split_at < limit // 3:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]

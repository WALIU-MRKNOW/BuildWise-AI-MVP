"""Concise, Telegram-safe BuildWise response formatting."""

from __future__ import annotations

import re

from bot.domain import Mode


TELEGRAM_SAFE_CHUNK = 3900

MODE_HEADERS = {
    Mode.STUDY: "📚 BUILDWISE · STUDY & SOLVE",
    Mode.SKETCH: "📐 BUILDWISE · SKETCH ANALYSIS",
    Mode.BUILDING: "🏗️ BUILDWISE · BUILDING ANALYSIS",
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


def format_response(mode: Mode, model_text: str) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", model_text.strip())
    if not cleaned:
        cleaned = "I could not produce a useful answer. Please try a clearer request."
    parts = [MODE_HEADERS[mode], cleaned]
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

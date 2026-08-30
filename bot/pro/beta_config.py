"""Strict configuration validation for the private BuildWise Pro beta."""

from __future__ import annotations

import os

from bot.config import Settings

PRO_BETA_MAX_TESTERS = 10
ADMIN_TELEGRAM_IDS_ENV = "BUILDWISE_ADMIN_TELEGRAM_IDS"


def _parse_strict_ids(raw: str, variable: str, *, max_unique: int | None = None) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    parts = raw.split(",")
    if any(not part.strip() for part in parts):
        raise ValueError(f"{variable} contains an empty Telegram ID")
    values: set[int] = set()
    for raw_value in parts:
        value = raw_value.strip()
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{variable} must contain numeric Telegram IDs") from exc
        if parsed <= 0:
            raise ValueError(f"{variable} must contain positive Telegram IDs")
        values.add(parsed)
    if max_unique is not None and len(values) > max_unique:
        raise ValueError(
            f"{variable} supports at most {max_unique} unique Telegram user IDs; got {len(values)}"
        )
    return frozenset(values)


def validated_pro_allowlist(settings: Settings) -> frozenset[int]:
    """Fail startup safely if the private beta allowlist is malformed or exceeds 10 users."""
    return _parse_strict_ids(
        settings.buildwise_pro_allowed_telegram_ids_raw,
        "BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS",
        max_unique=PRO_BETA_MAX_TESTERS,
    )


def validated_admin_telegram_ids() -> frozenset[int]:
    """Telegram identity binding for admin-only bot commands; HTTP admin auth remains unchanged."""
    return _parse_strict_ids(os.getenv(ADMIN_TELEGRAM_IDS_ENV, ""), ADMIN_TELEGRAM_IDS_ENV)

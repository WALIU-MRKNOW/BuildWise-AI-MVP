"""Validated environment configuration. Secrets never leave the server."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    runtime_mode: Literal["polling", "webhook"] = Field(
        default="polling", validation_alias="RUNTIME_MODE"
    )
    telegram_webhook_secret: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_WEBHOOK_SECRET"
    )
    telegram_webhook_base_url: str = Field(
        default="", validation_alias="TELEGRAM_WEBHOOK_BASE_URL"
    )
    internal_cron_secret: SecretStr | None = Field(
        default=None, validation_alias="INTERNAL_CRON_SECRET"
    )
    openai_api_key: SecretStr = Field(validation_alias="OPENAI_API_KEY")
    supabase_url: str = Field(validation_alias="SUPABASE_URL")
    supabase_secret_key: SecretStr = Field(
        validation_alias=AliasChoices(
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        )
    )

    admin_username: str = Field(default="", validation_alias="ADMIN_USERNAME")
    admin_password: SecretStr | None = Field(default=None, validation_alias="ADMIN_PASSWORD")
    admin_telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="ADMIN_TELEGRAM_BOT_TOKEN"
    )

    # BuildWise Pro is disabled until explicitly enabled in a reviewed environment.
    buildwise_pro_enabled: bool = Field(default=False, validation_alias="BUILDWISE_PRO_ENABLED")
    # Keep the browser preview independently disabled in production Telegram deployments.
    buildwise_pro_web_enabled: bool = Field(
        default=False, validation_alias="BUILDWISE_PRO_WEB_ENABLED"
    )
    buildwise_pro_allowed_telegram_ids_raw: str = Field(
        default="", validation_alias="BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS"
    )
    buildwise_pro_access_key: SecretStr | None = Field(
        default=None, validation_alias="BUILDWISE_PRO_ACCESS_KEY"
    )
    buildwise_pro_session_secret: SecretStr | None = Field(
        default=None, validation_alias="BUILDWISE_PRO_SESSION_SECRET"
    )
    buildwise_pro_session_ttl_seconds: int = Field(
        default=28800,
        ge=300,
        le=86400,
        validation_alias="BUILDWISE_PRO_SESSION_TTL_SECONDS",
    )
    buildwise_pro_daily_request_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        validation_alias="BUILDWISE_PRO_DAILY_REQUEST_LIMIT",
    )
    buildwise_pro_max_file_size_mb: int = Field(
        default=10,
        ge=1,
        le=20,
        validation_alias="BUILDWISE_PRO_MAX_FILE_SIZE_MB",
    )

    openai_model: str = Field(default="gpt-5.6-terra", validation_alias="OPENAI_MODEL")
    openai_max_output_tokens: int = Field(
        default=1200,
        ge=200,
        le=5000,
        validation_alias="OPENAI_MAX_OUTPUT_TOKENS",
    )
    openai_image_detail: Literal["auto", "low", "high"] = Field(
        default="auto", validation_alias="OPENAI_IMAGE_DETAIL"
    )
    openai_timeout_seconds: float = Field(
        default=90, ge=10, le=300, validation_alias="OPENAI_TIMEOUT_SECONDS"
    )

    supabase_storage_bucket: str = Field(
        default="buildwise-uploads", validation_alias="SUPABASE_STORAGE_BUCKET"
    )
    max_file_size_mb: int = Field(
        default=10, ge=1, le=20, validation_alias="MAX_FILE_SIZE_MB"
    )
    history_turns: int = Field(default=8, ge=0, le=20, validation_alias="HISTORY_TURNS")
    daily_ai_request_limit: int = Field(
        default=20, ge=1, le=1000, validation_alias="DAILY_AI_REQUEST_LIMIT"
    )
    request_cooldown_seconds: float = Field(
        default=2.0, ge=0, le=60, validation_alias="REQUEST_COOLDOWN_SECONDS"
    )
    reminder_poll_seconds: int = Field(
        default=60, ge=30, le=3600, validation_alias="REMINDER_POLL_SECONDS"
    )
    allowed_user_ids_raw: str = Field(default="", validation_alias="ALLOWED_USER_IDS")

    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )

    @field_validator("openai_model", "supabase_storage_bucket")
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be blank")
        return value.strip()

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith("https://"):
            raise ValueError("SUPABASE_URL must begin with https://")
        return cleaned

    @field_validator("telegram_webhook_base_url")
    @classmethod
    def validate_webhook_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if cleaned and not cleaned.startswith("https://"):
            raise ValueError("TELEGRAM_WEBHOOK_BASE_URL must begin with https://")
        return cleaned

    @staticmethod
    def _parse_id_list(raw: str, variable: str) -> frozenset[int]:
        if not raw.strip():
            return frozenset()
        values: set[int] = set()
        for raw_value in raw.split(","):
            value = raw_value.strip()
            if not value:
                continue
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError(f"{variable} must contain numeric Telegram IDs") from exc
            if parsed <= 0:
                raise ValueError(f"{variable} must contain positive Telegram IDs")
            values.add(parsed)
        return frozenset(values)

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return self._parse_id_list(self.allowed_user_ids_raw, "ALLOWED_USER_IDS")

    @property
    def buildwise_pro_allowed_telegram_ids(self) -> frozenset[int]:
        return self._parse_id_list(
            self.buildwise_pro_allowed_telegram_ids_raw,
            "BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS",
        )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def buildwise_pro_max_file_size_bytes(self) -> int:
        return self.buildwise_pro_max_file_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

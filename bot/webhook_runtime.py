"""Cloud Run/Railway runtime using Telegram webhooks and a protected reminder tick."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
from types import SimpleNamespace
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from telegram import Update
import uvicorn

from bot.admin import create_admin_router, log_admin_configuration
from bot.api import RuntimeHealth
from bot.app import BuildWiseComponents, build_components
from bot.config import Settings, load_settings
from bot.pro.router import create_pro_router
from bot.runtime import configure_logging
from bot.security import (
    MAX_TELEGRAM_WEBHOOK_BYTES,
    add_security_headers,
    log_security_events,
    read_limited_body,
    validate_json_request,
)


def _required_secret(value: object, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required when RUNTIME_MODE=webhook")
    secret = value.get_secret_value().strip()  # type: ignore[union-attr]
    if len(secret) < 24:
        raise ValueError(f"{name} must contain at least 24 characters")
    return secret


def _webhook_url(settings: Settings) -> str:
    if settings.telegram_webhook_base_url:
        base = settings.telegram_webhook_base_url
    else:
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
        if not railway_domain:
            raise ValueError(
                "TELEGRAM_WEBHOOK_BASE_URL or RAILWAY_PUBLIC_DOMAIN is required when RUNTIME_MODE=webhook"
            )
        base = f"https://{railway_domain}"
    return f"{base}/telegram/webhook"


def create_webhook_api(settings: Settings) -> FastAPI:
    webhook_secret = _required_secret(
        settings.telegram_webhook_secret, "TELEGRAM_WEBHOOK_SECRET"
    )
    cron_secret = _required_secret(
        settings.internal_cron_secret, "INTERNAL_CRON_SECRET"
    )
    webhook_url = _webhook_url(settings)
    components: BuildWiseComponents = build_components(settings)
    health = RuntimeHealth()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        telegram = components.telegram
        initialized = False
        started = False
        try:
            await telegram.initialize()
            initialized = True
            await components.handlers.post_init(telegram)
            await telegram.start()
            started = True
            await telegram.bot.set_webhook(
                url=webhook_url,
                secret_token=webhook_secret,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            health.ready = True
            logging.getLogger(__name__).info(
                "BuildWise webhook runtime ready model=%s endpoint=%s",
                settings.openai_model,
                webhook_url,
            )
            yield
        finally:
            health.ready = False
            # Intentionally keep the Telegram webhook registered during restarts.
            if started:
                await telegram.stop()
            if initialized:
                await telegram.shutdown()
            await components.handlers.shutdown(telegram)

    api = FastAPI(
        title="BuildWise AI",
        description="Telegram webhook service for BuildWise AI.",
        version="1.6.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    api.middleware("http")(add_security_headers)
    api.middleware("http")(log_security_events)

    @api.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "BuildWise AI",
            "tagline": "Plan • Learn • Analyze • Build Better",
        }

    @api.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {
            "status": "ok" if health.ready else "starting",
            "service": "buildwise-ai",
            "runtime": "webhook",
        }

    @api.post("/telegram/webhook", status_code=status.HTTP_200_OK)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        # Telegram's secret header is the primary authentication boundary for
        # this public endpoint. Compare in constant time and do not log it.
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, webhook_secret
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        validate_json_request(request, max_bytes=MAX_TELEGRAM_WEBHOOK_BYTES)
        try:
            raw_body = await read_limited_body(
                request, max_bytes=MAX_TELEGRAM_WEBHOOK_BYTES
            )
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ValueError("Telegram update must be a JSON object")
            update = Update.de_json(payload, components.telegram.bot)
            if update is None:
                raise ValueError("Telegram update was empty")
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Telegram update",
            ) from exc
        await components.telegram.process_update(update)
        return Response(status_code=status.HTTP_200_OK)

    @api.post("/internal/reminders", status_code=status.HTTP_204_NO_CONTENT)
    async def reminder_tick(
        x_buildwise_cron_secret: str | None = Header(default=None),
    ) -> Response:
        if not x_buildwise_cron_secret or not hmac.compare_digest(
            x_buildwise_cron_secret, cron_secret
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        context = SimpleNamespace(bot=components.telegram.bot)
        await components.reminders.dispatch(context)  # type: ignore[arg-type]
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    log_admin_configuration(settings)
    api.include_router(create_admin_router(settings))
    if settings.buildwise_pro_enabled and settings.buildwise_pro_web_enabled:
        api.include_router(create_pro_router(settings))
    return api


def run() -> None:
    try:
        settings = load_settings()
        if settings.runtime_mode != "webhook":
            raise ValueError("RUNTIME_MODE must be webhook for this runtime")
        api = create_webhook_api(settings)
    except (ValidationError, ValueError) as exc:
        raise SystemExit(f"Configuration error.\n{exc}") from exc
    configure_logging(settings.log_level)
    uvicorn.run(
        api,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
        server_header=False,
    )

"""Cloud Run runtime using Telegram webhooks and a protected reminder tick."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import logging
from types import SimpleNamespace
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from telegram import Update
import uvicorn

from bot.api import RuntimeHealth
from bot.app import BuildWiseComponents, build_components
from bot.config import Settings, load_settings
from bot.runtime import configure_logging


def _required_secret(value: object, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required when RUNTIME_MODE=webhook")
    secret = value.get_secret_value().strip()  # type: ignore[union-attr]
    if len(secret) < 24:
        raise ValueError(f"{name} must contain at least 24 characters")
    return secret


def create_webhook_api(settings: Settings) -> FastAPI:
    webhook_secret = _required_secret(
        settings.telegram_webhook_secret, "TELEGRAM_WEBHOOK_SECRET"
    )
    cron_secret = _required_secret(
        settings.internal_cron_secret, "INTERNAL_CRON_SECRET"
    )
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
            health.ready = True
            logging.getLogger(__name__).info(
                "BuildWise webhook runtime ready model=%s", settings.openai_model
            )
            yield
        finally:
            health.ready = False
            if started:
                await telegram.stop()
            if initialized:
                await telegram.shutdown()
            await components.handlers.shutdown(telegram)

    api = FastAPI(
        title="BuildWise AI",
        description="Telegram webhook service for BuildWise AI.",
        version="1.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

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
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, webhook_secret
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        try:
            payload = await request.json()
            update = Update.de_json(payload, components.telegram.bot)
        except (TypeError, ValueError) as exc:
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
    )

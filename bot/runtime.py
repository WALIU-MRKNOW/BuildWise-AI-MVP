"""Run Telegram polling and FastAPI together in one portable asyncio process."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from telegram import Update
import uvicorn

from bot.api import RuntimeHealth, create_api
from bot.app import build_components
from bot.config import Settings, load_settings


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, level),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run_async(settings: Settings) -> None:
    components = build_components(settings)
    telegram = components.telegram
    health = RuntimeHealth()
    server = uvicorn.Server(
        uvicorn.Config(
            create_api(health),
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )
    initialized = False
    started = False
    polling = False
    try:
        await telegram.initialize()
        initialized = True
        await components.handlers.post_init(telegram)
        if telegram.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await telegram.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        polling = True
        await telegram.start()
        started = True
        health.ready = True
        logging.getLogger(__name__).info(
            "BuildWise AI ready on port=%s model=%s",
            settings.port,
            settings.openai_model,
        )
        await server.serve()
    finally:
        health.ready = False
        if polling and telegram.updater is not None:
            await telegram.updater.stop()
        if started:
            await telegram.stop()
        if initialized:
            await telegram.shutdown()
        await components.handlers.shutdown(telegram)


def run() -> None:
    try:
        settings = load_settings()
        _ = settings.allowed_user_ids
    except (ValidationError, ValueError) as exc:
        raise SystemExit(
            "Configuration error. Copy .env.example to .env and check every value.\n"
            f"{exc}"
        ) from exc
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_async(settings))
    except KeyboardInterrupt:
        pass

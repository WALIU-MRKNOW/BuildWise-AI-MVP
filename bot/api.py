"""FastAPI surface used by health checks and the private admin dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from bot.admin import create_admin_router, log_admin_configuration
from bot.config import Settings


@dataclass(slots=True)
class RuntimeHealth:
    ready: bool = False


def create_api(health: RuntimeHealth, settings: Settings) -> FastAPI:
    api = FastAPI(
        title="BuildWise AI",
        description="Health service for the BuildWise Telegram MVP.",
        version="1.1.0",
        docs_url=None,
        redoc_url=None,
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
        }

    log_admin_configuration(settings)
    api.include_router(create_admin_router(settings))
    return api

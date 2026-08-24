"""Small FastAPI surface used by Railway health checks."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI


@dataclass(slots=True)
class RuntimeHealth:
    ready: bool = False


def create_api(health: RuntimeHealth) -> FastAPI:
    api = FastAPI(
        title="BuildWise AI",
        description="Health service for the BuildWise Telegram MVP.",
        version="1.0.0",
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

    return api

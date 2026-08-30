"""Privacy-preserving operational analytics for the BuildWise Pro private beta."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


class ProBetaAnalyticsError(RuntimeError):
    pass


class ProBetaAnalyticsService:
    """Persist only safe Pro beta metadata; never accept engineering content or files."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.supabase_url
        self._key = settings.supabase_secret_key.get_secret_value()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = _auth_headers(self._key)
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=10.0),
        ) as client:
            try:
                response = await client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                raise ProBetaAnalyticsError("Pro beta analytics request failed") from exc
        if response.is_error:
            raise ProBetaAnalyticsError(
                f"Pro beta analytics request failed ({response.status_code})"
            )
        return response

    async def record_started(self, *, session_id: UUID, telegram_id: int) -> None:
        await self._request(
            "POST",
            "/rest/v1/pro_beta_reports",
            json={
                "session_id": str(session_id),
                "telegram_id": telegram_id,
                "generation_status": "started",
            },
            prefer="return=minimal",
        )

    async def record_generation(
        self,
        *,
        session_id: UUID,
        status: str,
        latency_ms: int,
        pdf_success: bool | None = None,
        docx_success: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if status not in {"success", "failed"}:
            raise ValueError("Invalid Pro generation status")
        payload: dict[str, Any] = {
            "generation_status": status,
            "generation_latency_ms": max(0, int(latency_ms)),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if pdf_success is not None:
            payload["pdf_success"] = bool(pdf_success)
        if docx_success is not None:
            payload["docx_success"] = bool(docx_success)
        # Cost remains unavailable until model pricing is implemented against reliable usage metadata.
        if input_tokens is not None:
            payload["input_tokens"] = max(0, int(input_tokens))
        if output_tokens is not None:
            payload["output_tokens"] = max(0, int(output_tokens))
        if total_tokens is not None:
            payload["total_tokens"] = max(0, int(total_tokens))
        await self._request(
            "PATCH",
            "/rest/v1/pro_beta_reports",
            params={"session_id": f"eq.{session_id}"},
            json=payload,
            prefer="return=minimal",
        )

    async def add_rating(
        self, *, session_id: UUID, telegram_id: int, rating: str
    ) -> None:
        if rating not in {"helpful", "needs_improvement"}:
            raise ValueError("Invalid feedback rating")
        await self._request(
            "POST",
            "/rest/v1/pro_beta_feedback",
            json={
                "session_id": str(session_id),
                "telegram_id": telegram_id,
                "rating": rating,
            },
            prefer="return=minimal",
        )

    async def add_text_feedback(
        self, *, session_id: UUID, telegram_id: int, text: str
    ) -> None:
        cleaned = text.strip()
        if not 1 <= len(cleaned) <= 1500:
            raise ValueError("Feedback must be between 1 and 1,500 characters")
        await self._request(
            "POST",
            "/rest/v1/pro_beta_feedback",
            json={
                "session_id": str(session_id),
                "telegram_id": telegram_id,
                "feedback_text": cleaned,
            },
            prefer="return=minimal",
        )

    async def summary(self) -> dict[str, Any]:
        reports_response = await self._request(
            "GET",
            "/rest/v1/pro_beta_reports",
            params={
                "select": "session_id,telegram_id,generation_status,generation_latency_ms,pdf_success,docx_success,input_tokens,output_tokens,total_tokens,started_at,completed_at",
                "limit": "10000",
            },
        )
        feedback_response = await self._request(
            "GET",
            "/rest/v1/pro_beta_feedback",
            params={
                "select": "session_id,telegram_id,rating,created_at",
                "limit": "10000",
            },
        )
        reports = reports_response.json()
        feedback = feedback_response.json()
        report_rows = reports if isinstance(reports, list) else []
        feedback_rows = feedback if isinstance(feedback, list) else []

        completed = [row for row in report_rows if row.get("generation_status") == "success"]
        failed = [row for row in report_rows if row.get("generation_status") == "failed"]
        latencies = [
            int(row["generation_latency_ms"])
            for row in report_rows
            if row.get("generation_latency_ms") is not None
        ]
        pdf_attempts = [row for row in completed if row.get("pdf_success") is not None]
        docx_attempts = [row for row in completed if row.get("docx_success") is not None]
        helpful = sum(1 for row in feedback_rows if row.get("rating") == "helpful")
        needs_improvement = sum(
            1 for row in feedback_rows if row.get("rating") == "needs_improvement"
        )
        sessions_with_feedback = {
            str(row.get("session_id")) for row in feedback_rows if row.get("session_id")
        }

        return {
            "testers_used": len(
                {int(row["telegram_id"]) for row in report_rows if row.get("telegram_id")}
            ),
            "reports_started": len(report_rows),
            "reports_completed": len(completed),
            "generation_failures": len(failed),
            "average_generation_latency_ms": (
                round(sum(latencies) / len(latencies)) if latencies else None
            ),
            "pdf_success_rate": (
                sum(1 for row in pdf_attempts if row.get("pdf_success")) / len(pdf_attempts)
                if pdf_attempts
                else None
            ),
            "docx_success_rate": (
                sum(1 for row in docx_attempts if row.get("docx_success")) / len(docx_attempts)
                if docx_attempts
                else None
            ),
            "helpful_count": helpful,
            "needs_improvement_count": needs_improvement,
            "feedback_rate": (
                len(sessions_with_feedback) / len(completed) if completed else 0.0
            ),
            "token_usage_available": any(
                row.get("total_tokens") is not None for row in report_rows
            ),
            "cost_estimation_available": False,
        }

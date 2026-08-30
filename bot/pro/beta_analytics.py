"""Metadata-only analytics for the private BuildWise Pro beta."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


class ProBetaAnalyticsError(RuntimeError):
    pass


class ProBetaAnalyticsService:
    """Persist only operational metadata and explicit tester feedback."""

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

    async def start_session(self, telegram_id: int) -> str:
        response = await self._request(
            "POST",
            "/rest/v1/pro_beta_sessions",
            json={"telegram_id": telegram_id, "generation_status": "started"},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise ProBetaAnalyticsError("Pro beta session could not be created")
        return str(rows[0]["id"])

    async def record_generation(
        self,
        *,
        session_id: str,
        telegram_id: int,
        success: bool,
        latency_ms: int,
        pdf_success: bool | None,
        docx_success: bool | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self._validate_uuid(session_id)
        payload: dict[str, Any] = {
            "generation_status": "success" if success else "failed",
            "generation_latency_ms": max(0, int(latency_ms)),
            "pdf_success": pdf_success,
            "docx_success": docx_success,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "approximate_cost_usd": None,
            "completed_at": "now()",
        }
        # PostgREST does not interpret now() in JSON. Omit completed_at and let the app
        # use a server-side RPC-free timestamp from the database by patching explicit UTC text.
        from datetime import datetime, timezone

        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        await self._request(
            "PATCH",
            "/rest/v1/pro_beta_sessions",
            params={"id": f"eq.{session_id}", "telegram_id": f"eq.{telegram_id}"},
            json=payload,
            prefer="return=minimal",
        )

    async def record_rating(
        self, *, session_id: str, telegram_id: int, rating: str
    ) -> None:
        self._validate_uuid(session_id)
        if rating not in {"helpful", "needs_improvement"}:
            raise ValueError("Invalid Pro feedback rating")
        await self._request(
            "POST",
            "/rest/v1/pro_beta_feedback",
            json={
                "session_id": session_id,
                "telegram_id": telegram_id,
                "feedback_type": "rating",
                "rating": rating,
            },
            prefer="return=minimal",
        )

    async def record_comment(
        self, *, session_id: str, telegram_id: int, feedback_text: str
    ) -> None:
        self._validate_uuid(session_id)
        cleaned = feedback_text.strip()
        if not 1 <= len(cleaned) <= 1000:
            raise ValueError("Feedback must be between 1 and 1,000 characters")
        await self._request(
            "POST",
            "/rest/v1/pro_beta_feedback",
            json={
                "session_id": session_id,
                "telegram_id": telegram_id,
                "feedback_type": "comment",
                "feedback_text": cleaned,
            },
            prefer="return=minimal",
        )

    async def session_belongs_to(self, session_id: str, telegram_id: int) -> bool:
        self._validate_uuid(session_id)
        response = await self._request(
            "GET",
            "/rest/v1/pro_beta_sessions",
            params={
                "id": f"eq.{session_id}",
                "telegram_id": f"eq.{telegram_id}",
                "select": "id",
                "limit": "1",
            },
        )
        rows = response.json()
        return bool(rows)

    async def stats(self, *, allowlisted_count: int) -> dict[str, Any]:
        sessions_response = await self._request(
            "GET",
            "/rest/v1/pro_beta_sessions",
            params={
                "select": "id,telegram_id,generation_status,generation_latency_ms,pdf_success,docx_success,input_tokens,output_tokens,total_tokens,started_at,completed_at",
                "order": "started_at.desc",
                "limit": "10000",
            },
        )
        feedback_response = await self._request(
            "GET",
            "/rest/v1/pro_beta_feedback",
            params={
                "select": "session_id,telegram_id,feedback_type,rating,created_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        sessions = sessions_response.json() if isinstance(sessions_response.json(), list) else []
        feedback = feedback_response.json() if isinstance(feedback_response.json(), list) else []

        completed = [row for row in sessions if row.get("generation_status") == "success"]
        failed = [row for row in sessions if row.get("generation_status") == "failed"]
        latencies = [
            int(row["generation_latency_ms"])
            for row in sessions
            if row.get("generation_latency_ms") is not None
        ]
        pdf_attempts = [row for row in completed if row.get("pdf_success") is not None]
        docx_attempts = [row for row in completed if row.get("docx_success") is not None]
        ratings = [row for row in feedback if row.get("feedback_type") == "rating"]
        helpful = sum(1 for row in ratings if row.get("rating") == "helpful")
        needs_improvement = sum(
            1 for row in ratings if row.get("rating") == "needs_improvement"
        )
        feedback_sessions = {str(row.get("session_id")) for row in feedback if row.get("session_id")}

        def rate(rows: list[dict[str, Any]], key: str) -> float | None:
            if not rows:
                return None
            return round(100.0 * sum(1 for row in rows if row.get(key) is True) / len(rows), 1)

        return {
            "allowlisted_testers": allowlisted_count,
            "testers_used": len({int(row["telegram_id"]) for row in sessions if row.get("telegram_id")}),
            "reports_started": len(sessions),
            "reports_completed": len(completed),
            "generation_failures": len(failed),
            "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "pdf_success_rate": rate(pdf_attempts, "pdf_success"),
            "docx_success_rate": rate(docx_attempts, "docx_success"),
            "helpful_count": helpful,
            "needs_improvement_count": needs_improvement,
            "feedback_rate": (
                round(100.0 * len(feedback_sessions) / len(completed), 1)
                if completed
                else 0.0
            ),
            "token_usage_available": any(row.get("total_tokens") is not None for row in sessions),
            "cost_estimation_available": False,
        }

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid Pro session reference") from exc

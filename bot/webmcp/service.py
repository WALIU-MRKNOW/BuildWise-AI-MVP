"""Server-side WebMCP tool orchestration using existing BuildWise services."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bot.config import Settings
from bot.services.openai_service import AIServiceError, OpenAIService
from bot.services.supabase_service import SupabaseError, SupabaseService

logger = logging.getLogger(__name__)


class PracticeQuizInput(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    count: int = Field(default=5, ge=1, le=10)

    @field_validator("topic")
    @classmethod
    def clean_topic(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("topic must contain at least two characters")
        return cleaned


class QuizQuestionOutput(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str


class QuizUsageOutput(BaseModel):
    used: int
    limit: int
    remaining: int


class PracticeQuizOutput(BaseModel):
    tool: Literal["generate_practice_quiz"] = "generate_practice_quiz"
    topic: str
    difficulty: str
    count: int
    questions: list[QuizQuestionOutput]
    usage: QuizUsageOutput


class WebMCPToolError(RuntimeError):
    def __init__(self, public_message: str, *, status_code: int = 500) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


class WebMCPQuizService:
    """Generate quizzes through the existing BuildWise OpenAI adapter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ai = OpenAIService(settings)

    @property
    def daily_limit(self) -> int:
        return self._settings.webmcp_daily_request_limit or self._settings.daily_ai_request_limit

    async def generate(
        self, request: PracticeQuizInput, *, subject_hash: str
    ) -> PracticeQuizOutput:
        database = SupabaseService(self._settings)
        reservation_id: str | None = None
        try:
            await database.initialize()
            rows = await database._rpc(  # Reuse the existing private server-side Supabase adapter.
                "consume_webmcp_usage",
                {
                    "p_subject_hash": subject_hash,
                    "p_usage_date": datetime.now(timezone.utc).date().isoformat(),
                    "p_limit": self.daily_limit,
                },
            )
            row = rows[0] if isinstance(rows, list) and rows else rows
            if not isinstance(row, dict):
                raise WebMCPToolError("BuildWise usage tracking is unavailable.", status_code=503)
            if not bool(row.get("allowed")):
                raise WebMCPToolError(
                    f"The WebMCP preview has used today's {self.daily_limit} AI requests.",
                    status_code=429,
                )
            reservation_id = str(row["reservation_id"])
            used = int(row["used"])

            try:
                questions = await self._ai.generate_quiz(
                    topic=request.topic,
                    difficulty=request.difficulty,
                    count=request.count,
                )
            except AIServiceError as exc:
                await database._rpc(
                    "refund_webmcp_usage", {"p_reservation_id": reservation_id}
                )
                reservation_id = None
                raise WebMCPToolError(exc.public_message, status_code=502) from exc

            await database._rpc(
                "finalize_webmcp_usage", {"p_reservation_id": reservation_id}
            )
            reservation_id = None
            logger.info(
                "WebMCP tool=generate_practice_quiz subject=%s difficulty=%s count=%s",
                subject_hash[:12],
                request.difficulty,
                request.count,
            )
            return PracticeQuizOutput(
                topic=request.topic,
                difficulty=request.difficulty,
                count=request.count,
                questions=[
                    QuizQuestionOutput(
                        question=item.question,
                        options=list(item.options),
                        correct_index=item.correct_index,
                        explanation=item.explanation,
                    )
                    for item in questions
                ],
                usage=QuizUsageOutput(
                    used=used,
                    limit=self.daily_limit,
                    remaining=max(0, self.daily_limit - used),
                ),
            )
        except WebMCPToolError:
            raise
        except SupabaseError as exc:
            logger.exception("WebMCP Supabase request failed")
            raise WebMCPToolError(
                "BuildWise usage tracking is temporarily unavailable.", status_code=503
            ) from exc
        finally:
            if reservation_id is not None:
                try:
                    await database._rpc(
                        "refund_webmcp_usage", {"p_reservation_id": reservation_id}
                    )
                except Exception:  # noqa: BLE001 - best-effort rollback during error cleanup.
                    logger.exception("Could not refund unfinished WebMCP usage reservation")
            await database.close()

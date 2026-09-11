"""Server-side OpenAI Responses API adapter for text, images, PDFs, and quizzes."""

from __future__ import annotations

import base64
import json
import logging
import random
import re
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    RateLimitError,
)

from bot.config import Settings
from bot.domain import (
    AnswerStyle,
    ConversationTurn,
    Mode,
    QuizQuestion,
    RequestPayload,
)
from bot.prompts import default_user_request, system_prompt
from bot.study_experience import StudyRequestKind, detect_study_request_kind

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class OpenAIService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
        )

    async def answer(
        self,
        mode: Mode,
        payload: RequestPayload,
        *,
        history: list[ConversationTurn] | None = None,
        answer_style: AnswerStyle = AnswerStyle.CONCISE,
    ) -> str:
        user_text = payload.text.strip() or default_user_request(
            mode, has_image=payload.has_image, has_pdf=payload.has_pdf
        )
        content: list[dict[str, object]] = [{"type": "input_text", "text": user_text}]
        if payload.has_image:
            mime_type = payload.image_mime_type or "image/jpeg"
            image_data = base64.b64encode(payload.image_bytes or b"").decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{image_data}",
                    "detail": self._settings.openai_image_detail,
                }
            )
        if payload.has_pdf:
            pdf_data = base64.b64encode(payload.pdf_bytes or b"").decode("ascii")
            content.append(
                {
                    "type": "input_file",
                    "filename": payload.filename or "document.pdf",
                    "file_data": f"data:application/pdf;base64,{pdf_data}",
                }
            )

        input_messages: list[dict[str, object]] = []
        for turn in history or []:
            if turn.role in {"user", "assistant"} and turn.content.strip():
                content_type = "output_text" if turn.role == "assistant" else "input_text"
                input_messages.append(
                    {
                        "role": turn.role,
                        "content": [{"type": content_type, "text": turn.content}],
                    }
                )
        input_messages.append({"role": "user", "content": content})

        is_calculation = (
            mode is Mode.STUDY
            and detect_study_request_kind(user_text) is StudyRequestKind.CALCULATION
        )
        if is_calculation:
            style = (
                "This is a student calculation. Always teach every meaningful step, "
                "show formulas, units, substitution, working, final answer, and a short "
                "learning check. Do not shorten the mathematical working even when the "
                "student's saved answer style is concise."
            )
        elif answer_style is AnswerStyle.DETAILED:
            style = "The student requested detail: include reasoning, full steps, and a short check."
        else:
            style = "Keep the response concise. Give the direct result first."

        logger.info(
            "OpenAI request mode=%s image=%s pdf=%s",
            mode.value,
            payload.has_image,
            payload.has_pdf,
        )
        output_budget = self._settings.openai_max_output_tokens
        if payload.has_pdf or mode is Mode.SKETCH or is_calculation or answer_style is AnswerStyle.DETAILED:
            output_budget = max(4000, output_budget)
        instructions = f"{system_prompt(mode)}\n\nResponse preference: {style}"
        try:
            response = await self._client.responses.create(
                model=self._settings.openai_model,
                instructions=instructions,
                input=input_messages,
                max_output_tokens=output_budget,
                store=False,
            )
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None)
            if getattr(response, "status", None) == "incomplete" and reason == "max_output_tokens":
                logger.warning("Answer reached output limit; retrying once mode=%s", mode.value)
                response = await self._client.responses.create(
                    model=self._settings.openai_model,
                    instructions=instructions + "\nComplete the answer within the available space. Avoid repeating the question or duplicating explanations; preserve essential working, conclusions and safety notes.",
                    input=input_messages,
                    max_output_tokens=min(8000, max(4000, output_budget * 2)),
                    store=False,
                )
        except OpenAIError as exc:
            raise self._translate_error(exc) from exc
        if getattr(response, "status", None) in {"incomplete", "failed", "cancelled"}:
            logger.warning("Answer did not complete mode=%s status=%s", mode.value, getattr(response, "status", None))
            raise AIServiceError("I couldn't finish this answer. Try asking about a specific section or a smaller page range. This unsuccessful request will be refunded to your daily allowance.")
        output = (response.output_text or "").strip()
        if not output:
            raise AIServiceError(
                "I could not produce an answer from that input. Try a clearer question or image."
            )
        return output

    async def generate_quiz(
        self, *, topic: str, difficulty: str, count: int
    ) -> list[QuizQuestion]:
        prompt = f"""
Create exactly {count} multiple-choice student practice questions.
Topic: {topic}
Difficulty: {difficulty}

Return only one valid JSON object with this shape:
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":2,"explanation":"..."}}]}}

Each question must have exactly four useful options. correct_index must be an
integer from 0 to 3. Vary the correct answer position across the quiz instead of
repeatedly placing it in the same option. Explanations must be short. Do not
include Markdown fences.
""".strip()
        try:
            response = await self._client.responses.create(
                model=self._settings.openai_model,
                instructions=(
                    "You create accurate educational practice. Return only strict JSON. "
                    "Do not add commentary before or after the JSON."
                ),
                input=prompt,
                max_output_tokens=min(
                    5000, max(self._settings.openai_max_output_tokens, count * 350)
                ),
                store=False,
            )
        except OpenAIError as exc:
            raise self._translate_error(exc) from exc
        try:
            data = self._parse_json(response.output_text or "")
            questions = [self._parse_question(item) for item in data["questions"]]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("OpenAI returned malformed quiz JSON")
            raise AIServiceError(
                "I could not create a reliable quiz just now. Please try the topic again."
            ) from exc
        if len(questions) != count:
            raise AIServiceError(
                "I could not create the requested number of quiz questions. Please try again."
            )
        return self._balance_quiz_answers(questions)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("No JSON object found")
        value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("Quiz JSON must be an object")
        return value

    @staticmethod
    def _parse_question(item: Any) -> QuizQuestion:
        if not isinstance(item, dict):
            raise ValueError("Question must be an object")
        options = item.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise ValueError("Question needs four options")
        correct_index = int(item["correct_index"])
        if correct_index not in range(4):
            raise ValueError("correct_index is invalid")
        question = str(item["question"]).strip()
        explanation = str(item["explanation"]).strip()
        option_values = tuple(str(option).strip() for option in options)
        if not question or not explanation or any(not option for option in option_values):
            raise ValueError("Question fields cannot be blank")
        return QuizQuestion(
            question=question,
            options=(
                option_values[0],
                option_values[1],
                option_values[2],
                option_values[3],
            ),
            correct_index=correct_index,
            explanation=explanation,
        )

    @staticmethod
    def _balance_quiz_answers(questions: list[QuizQuestion]) -> list[QuizQuestion]:
        """Spread correct answers across A-D while preserving question accuracy."""
        if len(questions) <= 1:
            return questions

        target_positions = [index % 4 for index in range(len(questions))]
        random.SystemRandom().shuffle(target_positions)
        balanced: list[QuizQuestion] = []

        for question, target_index in zip(questions, target_positions, strict=True):
            options = list(question.options)
            correct_answer = options.pop(question.correct_index)
            options.insert(target_index, correct_answer)
            balanced.append(
                QuizQuestion(
                    question=question.question,
                    options=(options[0], options[1], options[2], options[3]),
                    correct_index=target_index,
                    explanation=question.explanation,
                )
            )
        return balanced

    @staticmethod
    def _translate_error(exc: OpenAIError) -> AIServiceError:
        if isinstance(exc, AuthenticationError):
            logger.error("OpenAI authentication failed")
            return AIServiceError(
                "BuildWise AI is not configured correctly. The owner should check the OpenAI key."
            )
        if isinstance(exc, RateLimitError):
            return AIServiceError(
                "BuildWise is busy or its AI allowance was reached. Please try again shortly."
            )
        if isinstance(exc, APITimeoutError):
            return AIServiceError(
                "That took too long. Try a smaller PDF, clearer image, or shorter request."
            )
        if isinstance(exc, APIConnectionError):
            return AIServiceError(
                "I could not reach the AI service. Please try again in a moment."
            )
        if isinstance(exc, BadRequestError):
            logger.error("OpenAI bad request: %s", str(exc))
            return AIServiceError(
                "I could not process that input. Try a clearer image, supported PDF, or shorter text."
            )
        logger.error("Unexpected OpenAI error: %s", exc.__class__.__name__)
        return AIServiceError("The AI service returned an error. Please try again shortly.")

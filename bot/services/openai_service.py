"""Server-side OpenAI Responses API adapter for text, images, PDFs, and quizzes."""

from __future__ import annotations

import base64
import json
import logging
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
                input_messages.append(
                    {
                        "role": turn.role,
                        "content": [{"type": "input_text", "text": turn.content}],
                    }
                )
        input_messages.append({"role": "user", "content": content})
        style = (
            "The student requested detail: include reasoning, full steps, and a short check."
            if answer_style is AnswerStyle.DETAILED
            else "Keep the response concise. Give the direct result first."
        )
        logger.info(
            "OpenAI request mode=%s image=%s pdf=%s",
            mode.value,
            payload.has_image,
            payload.has_pdf,
        )
        try:
            response = await self._client.responses.create(
                model=self._settings.openai_model,
                instructions=f"{system_prompt(mode)}\n\nResponse preference: {style}",
                input=input_messages,
                max_output_tokens=self._settings.openai_max_output_tokens,
                store=False,
            )
        except OpenAIError as exc:
            raise self._translate_error(exc) from exc
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
{{"questions":[{{"question":"...","options":["...","...","...","..."],"correct_index":0,"explanation":"..."}}]}}

Each question must have exactly four useful options. correct_index must be an
integer from 0 to 3. Explanations must be short. Do not include Markdown fences.
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
        return questions

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
            return AIServiceError(
                "I could not process that input. Try a clearer image, supported PDF, or shorter text."
            )
        logger.error("Unexpected OpenAI error: %s", exc.__class__.__name__)
        return AIServiceError("The AI service returned an error. Please try again shortly.")

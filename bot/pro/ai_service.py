"""BuildWise Pro report generation using the existing BuildWise OpenAI adapter."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

from openai import OpenAIError
from pydantic import ValidationError

from bot.services.openai_service import AIServiceError, OpenAIService
from bot.pro.models import ProfessionalReport, ProfessionalReportRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProFile:
    filename: str
    mime_type: str
    data: bytes


class ProfessionalReportService(OpenAIService):
    """Adds a Pro workflow without changing existing student AI methods."""

    async def generate_professional_report(
        self,
        *,
        request: ProfessionalReportRequest,
        drawing: ProFile,
        template: ProFile | None = None,
    ) -> ProfessionalReport:
        text = f"""
Create a DRAFT professional engineering/inspection report from only the supplied material.

Project/document information supplied by user:
{request.project_information or '[not supplied]'}

Site notes supplied by user:
{request.site_notes}

Engineering code/standard reference supplied by user:
{request.standard_reference or '[none supplied]'}

Rules:
- Never invent dimensions, loads, site conditions, observations, drawing details, code clauses, standard requirements, calculations, approvals, signatures, credentials, or compliance conclusions.
- Clearly separate observed/supplied facts from assumptions and recommendations.
- Anything unclear, unreadable, missing, or uncertain must be listed under uncertainties_missing_information.
- A named code/standard is only a user-supplied reference unless its actual text is included in an uploaded source. Do not quote or fabricate clauses.
- Do not state that the project complies with a code or standard unless the supplied evidence directly establishes it; otherwise require professional verification.
- If a company template PDF is supplied, follow its visible section order/headings as closely as practical while retaining mandatory limitations and professional-review language.
- This is draft engineering assistance, never signed/certified/approved work.

Return ONLY one JSON object with these keys:
project_document_information (array of strings),
summary (string),
observations (array of strings),
drawing_sketch_findings (array of strings),
site_note_findings (array of strings),
relevant_technical_considerations (array of strings),
referenced_code_standard (array of strings),
recommendations (array of strings),
limitations_assumptions (array of strings),
uncertainties_missing_information (array of strings),
review_signoff (string),
professional_review_notice (string),
template_structure_used (boolean).
""".strip()

        content: list[dict[str, object]] = [{"type": "input_text", "text": text}]
        content.append(self._file_content(drawing, label="drawing or sketch"))
        if template is not None:
            content.append(self._file_content(template, label="company report template"))

        logger.info(
            "BuildWise Pro report request drawing_type=%s template=%s standard_ref=%s",
            drawing.mime_type,
            bool(template),
            bool(request.standard_reference),
        )
        try:
            response = await self._client.responses.create(
                model=self._settings.openai_model,
                instructions=(
                    "You are BuildWise Pro's professional report drafting assistant. "
                    "Use only supplied evidence. Flag uncertainty instead of guessing. "
                    "Return strict JSON only. Never claim professional certification or approval."
                ),
                input=[{"role": "user", "content": content}],
                max_output_tokens=min(5000, max(2400, self._settings.openai_max_output_tokens)),
                store=False,
            )
        except OpenAIError as exc:
            raise self._translate_error(exc) from exc

        try:
            data = self._parse_json(response.output_text or "")
            report = ProfessionalReport.model_validate(data)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("BuildWise Pro returned malformed report JSON")
            raise AIServiceError(
                "I could not create a reliable structured draft report from those inputs. "
                "Please check the files and try again."
            ) from exc
        return report

    @staticmethod
    def _file_content(file: ProFile, *, label: str) -> dict[str, object]:
        encoded = base64.b64encode(file.data).decode("ascii")
        if file.mime_type.startswith("image/"):
            return {
                "type": "input_image",
                "image_url": f"data:{file.mime_type};base64,{encoded}",
                "detail": "high",
            }
        if file.mime_type == "application/pdf":
            return {
                "type": "input_file",
                "filename": file.filename,
                "file_data": f"data:application/pdf;base64,{encoded}",
            }
        raise AIServiceError(f"Unsupported {label} file type.")

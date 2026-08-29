"""Validated BuildWise Pro request and structured report models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


PROFESSIONAL_REVIEW_NOTICE = (
    "DRAFT ENGINEERING ASSISTANCE ONLY — Calculations, code compliance, structural "
    "conclusions, design decisions, and drawings must be reviewed and approved by a "
    "suitably qualified professional before use on a real project. This output is not "
    "signed, certified, or professionally approved."
)

REVIEW_SIGNOFF_TEXT = (
    "Draft prepared with BuildWise Pro AI assistance. Professional review required before "
    "project use. Reviewed by: ____________________  Date: __________  "
    "Professional registration / role: ____________________."
)


class ProfessionalReportRequest(BaseModel):
    project_information: str = Field(default="", max_length=2000)
    site_notes: str = Field(min_length=3, max_length=16000)
    standard_reference: str = Field(default="", max_length=1200)

    @field_validator("project_information", "site_notes", "standard_reference")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class TemplateSection(BaseModel):
    heading: str = Field(min_length=1, max_length=240)
    content: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("heading")
    @classmethod
    def clean_heading(cls, value: str) -> str:
        return value.strip()

    @field_validator("content")
    @classmethod
    def clean_content(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]


class ProfessionalReport(BaseModel):
    project_document_information: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field(min_length=1, max_length=6000)
    observations: list[str] = Field(default_factory=list, max_length=50)
    drawing_sketch_findings: list[str] = Field(default_factory=list, max_length=50)
    site_note_findings: list[str] = Field(default_factory=list, max_length=50)
    relevant_technical_considerations: list[str] = Field(default_factory=list, max_length=50)
    referenced_code_standard: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=50)
    limitations_assumptions: list[str] = Field(default_factory=list, max_length=50)
    uncertainties_missing_information: list[str] = Field(default_factory=list, max_length=50)
    review_signoff: str = Field(default=REVIEW_SIGNOFF_TEXT, max_length=3000)
    professional_review_notice: str = Field(default=PROFESSIONAL_REVIEW_NOTICE)
    template_structure_used: bool = False
    template_sections: list[TemplateSection] = Field(default_factory=list, max_length=40)

    @field_validator(
        "project_document_information",
        "observations",
        "drawing_sketch_findings",
        "site_note_findings",
        "relevant_technical_considerations",
        "referenced_code_standard",
        "recommendations",
        "limitations_assumptions",
        "uncertainties_missing_information",
    )
    @classmethod
    def clean_items(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    @field_validator("professional_review_notice")
    @classmethod
    def force_review_notice(cls, _: str) -> str:
        return PROFESSIONAL_REVIEW_NOTICE

    @field_validator("review_signoff")
    @classmethod
    def force_safe_signoff(cls, _: str) -> str:
        return REVIEW_SIGNOFF_TEXT

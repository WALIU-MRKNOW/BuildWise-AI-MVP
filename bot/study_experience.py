"""Helpers for the student-facing study and calculation experience."""

from __future__ import annotations

import re
from enum import Enum


class StudyRequestKind(str, Enum):
    CALCULATION = "calculation"
    TOPIC = "topic"


_EXPLICIT_CALCULATION = re.compile(
    r"\b(calculate|compute|solve|work\s*out|evaluate|find\s+the|determine\s+the|"
    r"how\s+many|how\s+much|estimate\s+the|quantity\s+of|take[- ]?off|cost\s+of)\b",
    flags=re.IGNORECASE,
)

_TECHNICAL_CALCULATION = re.compile(
    r"\b(area|volume|perimeter|ratio|gradient|slope|bearing|level|reaction|"
    r"bending\s+moment|shear\s+force|load|stress|strain|flow|discharge|pressure|"
    r"density|mass|weight|rate|cost|wastage|productivity|duration|manpower|"
    r"blockwork|brickwork|concrete|excavation|earthwork|reinforcement|formwork|"
    r"roofing|plaster|screed|paint|tiles?|quantity|measurement)\b",
    flags=re.IGNORECASE,
)

_NUMBER_WITH_OPTIONAL_UNIT = re.compile(
    r"(?<!\w)\d+(?:\.\d+)?\s*(?:mm|cm|m|km|m2|m3|m²|m³|kg|g|t|kn|kn/m|"
    r"kn/m2|kn/m²|n|pa|kpa|mpa|l|l/s|m/s|m/s2|m/s²|%|naira|₦)?\b",
    flags=re.IGNORECASE,
)


def detect_study_request_kind(text: str) -> StudyRequestKind:
    """Classify a student request without an additional AI call.

    The classifier intentionally requires either an explicit calculation verb or
    a combination of numeric data and a technical quantity. This keeps theory
    questions such as "explain bending moment" in topic mode.
    """

    normalized = " ".join((text or "").split())
    if not normalized:
        return StudyRequestKind.TOPIC
    if _EXPLICIT_CALCULATION.search(normalized):
        return StudyRequestKind.CALCULATION
    numbers = _NUMBER_WITH_OPTIONAL_UNIT.findall(normalized)
    if len(numbers) >= 2 and _TECHNICAL_CALCULATION.search(normalized):
        return StudyRequestKind.CALCULATION
    if any(symbol in normalized for symbol in ("=", "+", "×", "÷")) and numbers:
        return StudyRequestKind.CALCULATION
    return StudyRequestKind.TOPIC


def strip_learning_marker(text: str) -> tuple[StudyRequestKind | None, str]:
    """Remove a legacy presentation marker if one is returned by the model."""

    cleaned = text.strip()
    upper = cleaned.upper()
    if upper.startswith("[[CALCULATION]]"):
        return StudyRequestKind.CALCULATION, cleaned[len("[[CALCULATION]]") :].lstrip()
    if upper.startswith("[[TOPIC]]"):
        return StudyRequestKind.TOPIC, cleaned[len("[[TOPIC]]") :].lstrip()
    return None, cleaned


def infer_study_answer_kind(text: str) -> tuple[StudyRequestKind | None, str]:
    """Infer the answer type from its teaching sections while keeping history clean."""

    marker_kind, cleaned = strip_learning_marker(text)
    if marker_kind is not None:
        return marker_kind, cleaned

    normalized = cleaned.lower()
    calculation_signals = (
        "given data",
        "formula / principle",
        "why this formula",
        "substitution",
        "step-by-step working",
        "final answer",
    )
    topic_signals = (
        "meaning",
        "key points",
        "construction example",
        "exam / study tip",
    )
    calc_score = sum(signal in normalized for signal in calculation_signals)
    topic_score = sum(signal in normalized for signal in topic_signals)
    if calc_score >= 2 and calc_score > topic_score:
        return StudyRequestKind.CALCULATION, cleaned
    if topic_score >= 2:
        return StudyRequestKind.TOPIC, cleaned
    return None, cleaned

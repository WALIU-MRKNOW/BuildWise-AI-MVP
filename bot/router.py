"""Fast, deterministic routing that avoids an extra AI classification call."""

from __future__ import annotations

from dataclasses import dataclass
import re

from bot.domain import Mode


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    mode: Mode | None
    reason: str

    @property
    def needs_clarification(self) -> bool:
        return self.mode is None


class RequestRouter:
    _STRONG_SKETCH_PATTERNS = (
        r"\b(floor ?plan|site plan|architectural (?:plan|sketch|drawing)|blueprint)\b",
        r"\b(hand[- ]?drawn sketch|section drawing|elevation drawing)\b",
    )
    _BUILDING_PATTERNS = (
        r"\b(building|construction|site|structure|structural|defect|damage)\b",
        r"\b(crack|spall(?:ing)?|damp|mold|mould|leak|corrosion|settlement)\b",
        r"\b(concrete|reinforcement|rebar|roof|drainage|foundation|column|beam)\b",
    )
    _SKETCH_PATTERNS = (
        r"\b(floor ?plan|site plan|architectural|sketch|drawing|blueprint)\b",
        r"\b(layout|circulation|zoning|space planning|elevation|section drawing)\b",
        r"\b(room|door|window|corridor|staircase|dimension|ventilation|lighting)\b",
    )
    _STUDY_PATTERNS = (
        r"\b(solve|calculate|answer|explain|study|homework|exam|question|equation)\b",
        r"\b(quiz|practice|revision|theory|formula|moment|force|math|physics)\b",
    )

    def route(
        self,
        text: str,
        *,
        selected_mode: Mode | None = None,
        has_image: bool = False,
        has_pdf: bool = False,
        filename: str | None = None,
    ) -> RoutingDecision:
        if selected_mode is not None:
            return RoutingDecision(selected_mode, "selected mode")

        normalized = " ".join(text.lower().split())
        filename_text = (filename or "").lower()
        searchable = f"{normalized} {filename_text}".strip()

        if self._matches(searchable, self._STRONG_SKETCH_PATTERNS):
            return RoutingDecision(Mode.SKETCH, "explicit drawing keywords")

        # Text-only technical questions should be treated as study requests even when
        # they contain construction nouns such as beam, concrete, structure, or
        # foundation. Previously those nouns won the routing decision first and sent
        # structural calculations into building-photo analysis, which can return no
        # usable answer when no image is attached.
        if normalized and not has_image and self._matches(searchable, self._STUDY_PATTERNS):
            return RoutingDecision(Mode.STUDY, "text study/calculation keywords")

        if self._matches(searchable, self._BUILDING_PATTERNS):
            return RoutingDecision(Mode.BUILDING, "building keywords")
        if self._matches(searchable, self._SKETCH_PATTERNS):
            return RoutingDecision(Mode.SKETCH, "sketch keywords")
        if self._matches(searchable, self._STUDY_PATTERNS):
            return RoutingDecision(Mode.STUDY, "study keywords")
        if has_pdf:
            return RoutingDecision(Mode.STUDY, "PDF defaults to study")
        if normalized and not has_image:
            return RoutingDecision(Mode.STUDY, "general text question")
        if has_image:
            return RoutingDecision(None, "unlabelled image")
        return RoutingDecision(Mode.STUDY, "default")

    @staticmethod
    def _matches(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

"""Parse machine-readable sketch correction guidance without exposing it to students."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


SKETCH_FIX_MARKER = "[[SKETCH_FIX_SPEC]]"
MAX_SKETCH_CHANGES = 10


@dataclass(frozen=True, slots=True)
class SketchChange:
    """One localized conceptual correction on the uploaded sketch.

    Coordinates are normalized to the original image with (0, 0) at the top-left.
    """

    x: float
    y: float
    w: float
    h: float
    label: str
    action: str


@dataclass(frozen=True, slots=True)
class SketchFixSpec:
    summary: str
    changes: tuple[SketchChange, ...]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _validated_change(value: Any) -> SketchChange | None:
    if not isinstance(value, dict):
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    w = _number(value.get("w"))
    h = _number(value.get("h"))
    if None in {x, y, w, h}:
        return None
    assert x is not None and y is not None and w is not None and h is not None
    x = _clamp(x)
    y = _clamp(y)
    w = _clamp(w, 0.01, 1.0)
    h = _clamp(h, 0.01, 1.0)
    if x + w > 1.0:
        w = max(0.01, 1.0 - x)
    if y + h > 1.0:
        h = max(0.01, 1.0 - y)
    label = " ".join(str(value.get("label") or "").split())[:80]
    action = " ".join(str(value.get("action") or "").split())[:220]
    if not label or not action:
        return None
    return SketchChange(x=x, y=y, w=w, h=h, label=label, action=action)


def parse_sketch_fix_spec(value: Any) -> SketchFixSpec | None:
    if not isinstance(value, dict):
        return None
    summary = " ".join(str(value.get("summary") or "").split())[:280]
    raw_changes = value.get("changes")
    changes: list[SketchChange] = []
    if isinstance(raw_changes, list):
        for raw in raw_changes[:MAX_SKETCH_CHANGES]:
            change = _validated_change(raw)
            if change is not None:
                changes.append(change)
    if not summary and not changes:
        return None
    return SketchFixSpec(summary=summary, changes=tuple(changes))


def extract_sketch_fix_spec(text: str) -> tuple[str, SketchFixSpec | None]:
    """Return the visible answer and an optional validated correction overlay spec."""

    raw = (text or "").strip()
    marker_index = raw.find(SKETCH_FIX_MARKER)
    if marker_index < 0:
        return raw, None

    visible = raw[:marker_index].rstrip()
    tail = raw[marker_index + len(SKETCH_FIX_MARKER) :].lstrip()
    if not tail:
        return visible, None

    try:
        value, _ = json.JSONDecoder().raw_decode(tail)
    except (json.JSONDecodeError, TypeError, ValueError):
        return visible, None
    return visible, parse_sketch_fix_spec(value)

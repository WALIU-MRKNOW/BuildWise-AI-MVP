"""In-memory PDF renderer for student worked solutions and study notes."""

from __future__ import annotations

from html import escape
from io import BytesIO
import re
import unicodedata

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from bot.formatting import clean_student_math
from bot.study_experience import StudyRequestKind, infer_study_answer_kind


_SYMBOL_REPLACEMENTS = {
    "×": " x ",
    "÷": " / ",
    "≤": " <= ",
    "≥": " >= ",
    "≈": " ~ ",
    "√": "sqrt ",
    "²": "^2",
    "³": "^3",
    "₦": "NGN ",
    "•": "-",
    "→": "->",
    "←": "<-",
    "↑": "^",
    "↓": "v",
    "—": "-",
    "–": "-",
    "’": "'",
    "“": '"',
    "”": '"',
    "│": "|",
    "║": "|",
    "─": "-",
    "═": "=",
    "┌": "+",
    "┐": "+",
    "└": "+",
    "┘": "+",
    "├": "+",
    "┤": "+",
    "┬": "+",
    "┴": "+",
    "┼": "+",
    "╔": "+",
    "╗": "+",
    "╚": "+",
    "╝": "+",
    "╠": "+",
    "╣": "+",
    "╦": "+",
    "╩": "+",
    "╬": "+",
}


def _ascii_pdf_text(value: str) -> str:
    text = value
    for source, target in _SYMBOL_REPLACEMENTS.items():
        text = text.replace(source, target)
    # ReportLab's built-in Helvetica/Courier fonts do not contain emoji glyphs.
    # Remove unsupported symbols rather than producing black squares.
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def _pdf_safe_text(value: str) -> str:
    text = _ascii_pdf_text(value)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _pdf_safe_diagram_text(value: str) -> str:
    """Convert diagram glyphs to PDF-safe ASCII without collapsing alignment."""

    text = _ascii_pdf_text(value).replace("\t", "    ")
    return text.rstrip()


def _is_heading(line: str) -> bool:
    plain = re.sub(r"^[^A-Za-z0-9]+", "", line).strip()
    labels = (
        "Problem",
        "Diagram",
        "Given Data",
        "Formula / Principle",
        "Why This Formula",
        "Substitution",
        "Step-by-Step Working",
        "Final Answer",
        "What To Remember",
        "Meaning",
        "Key Points",
        "Construction Example",
        "Exam / Study Tip",
    )
    return any(plain.lower().startswith(label.lower()) for label in labels)


def _is_diagram_heading(line: str) -> bool:
    plain = re.sub(r"^[^A-Za-z0-9]+", "", line).strip().lower()
    return plain.startswith("diagram")


def render_student_solution_pdf(question: str, answer: str) -> bytes:
    """Render the latest BuildWise student answer as a clean A4 PDF in memory."""

    kind, body = infer_study_answer_kind(answer)
    body = clean_student_math(body)
    is_calculation = kind is StudyRequestKind.CALCULATION
    title = "BuildWise AI - Worked Solution" if is_calculation else "BuildWise AI - Study Notes"

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="BuildWise AI",
    )
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.alignment = TA_CENTER
    title_style.fontName = "Helvetica-Bold"
    body_style = ParagraphStyle(
        "StudentBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        spaceAfter=3,
    )
    heading_style = ParagraphStyle(
        "StudentHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=8,
        spaceAfter=4,
    )
    diagram_style = ParagraphStyle(
        "StudentDiagram",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8.5,
        leading=11,
        leftIndent=4,
        rightIndent=4,
        spaceBefore=2,
        spaceAfter=7,
    )
    meta_style = ParagraphStyle(
        "StudentMeta",
        parent=body_style,
        textColor=colors.HexColor("#444444"),
        fontSize=9,
    )

    story: list[object] = [
        Paragraph(escape(_pdf_safe_text(title)), title_style),
        Spacer(1, 4 * mm),
    ]

    question_box = Table(
        [[Paragraph(f"<b>Question</b><br/>{escape(_pdf_safe_text(question))}", body_style)]],
        colWidths=[170 * mm],
    )
    question_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#B8B8B8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F7F7")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.extend([question_box, Spacer(1, 5 * mm)])

    in_diagram = False
    diagram_lines: list[str] = []

    def flush_diagram() -> None:
        if not diagram_lines:
            return
        while diagram_lines and not diagram_lines[-1]:
            diagram_lines.pop()
        if diagram_lines:
            story.append(
                Preformatted(
                    escape("\n".join(diagram_lines)),
                    diagram_style,
                    maxLineLength=92,
                )
            )
        diagram_lines.clear()

    for raw_line in body.splitlines():
        normal_line = _pdf_safe_text(raw_line)
        if normal_line and _is_heading(normal_line):
            if in_diagram:
                flush_diagram()
            in_diagram = _is_diagram_heading(normal_line)
            story.append(Paragraph(escape(normal_line), heading_style))
            continue

        if in_diagram:
            diagram_lines.append(_pdf_safe_diagram_text(raw_line))
            continue

        if not normal_line:
            story.append(Spacer(1, 2 * mm))
            continue
        story.append(Paragraph(escape(normal_line), body_style))

    if in_diagram:
        flush_diagram()

    story.extend(
        [
            Spacer(1, 6 * mm),
            Paragraph(
                "Educational note: Check important design, site, code, and professional decisions with your course instructor or an appropriately qualified professional.",
                meta_style,
            ),
        ]
    )
    document.build(story)
    return buffer.getvalue()

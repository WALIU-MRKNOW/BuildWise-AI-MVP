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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from bot.formatting import clean_student_math
from bot.study_experience import StudyRequestKind, strip_learning_marker


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
    "—": "-",
    "–": "-",
    "’": "'",
    "“": '"',
    "”": '"',
}


def _pdf_safe_text(value: str) -> str:
    text = value
    for source, target in _SYMBOL_REPLACEMENTS.items():
        text = text.replace(source, target)
    # ReportLab's built-in Helvetica does not contain emoji glyphs. Remove emoji
    # and other unsupported symbols rather than producing black squares.
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _is_heading(line: str) -> bool:
    plain = re.sub(r"^[^A-Za-z0-9]+", "", line).strip()
    labels = (
        "Problem",
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


def render_student_solution_pdf(question: str, answer: str) -> bytes:
    """Render the latest BuildWise student answer as a clean A4 PDF in memory."""

    kind, body = strip_learning_marker(answer)
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

    for raw_line in body.splitlines():
        line = _pdf_safe_text(raw_line)
        if not line:
            story.append(Spacer(1, 2 * mm))
            continue
        style = heading_style if _is_heading(line) else body_style
        story.append(Paragraph(escape(line), style))

    story.extend(
        [
            Spacer(1, 6 * mm),
            Paragraph(
                "Educational note: Check important design, site, code, and professional decisions with your lecturer or an appropriately qualified professional.",
                meta_style,
            ),
        ]
    )
    document.build(story)
    return buffer.getvalue()

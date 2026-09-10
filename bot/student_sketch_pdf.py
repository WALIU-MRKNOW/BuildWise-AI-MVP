"""Compact before/after PDF output for BuildWise student sketch reviews."""

from __future__ import annotations

from html import escape
from io import BytesIO
import re
import unicodedata

from PIL import Image as PILImage
from PIL import ImageDraw, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from bot.sketch_experience import SketchFixSpec


MAX_IMAGE_EDGE = 1500
JPEG_QUALITY = 68


def _safe_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _prepared_image(data: bytes) -> PILImage.Image:
    source = PILImage.open(BytesIO(data))
    source = ImageOps.exif_transpose(source).convert("RGB")
    width, height = source.size
    scale = min(1.0, MAX_IMAGE_EDGE / max(width, height))
    if scale < 1.0:
        source = source.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            PILImage.Resampling.LANCZOS,
        )
    return source


def _jpeg_bytes(image: PILImage.Image) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )
    return buffer.getvalue()


def _annotated_revision(source: PILImage.Image, spec: SketchFixSpec | None) -> PILImage.Image:
    revised = source.copy()
    if spec is None or not spec.changes:
        return revised

    draw = ImageDraw.Draw(revised, "RGBA")
    width, height = revised.size
    line_width = max(3, round(min(width, height) * 0.006))
    badge_radius = max(12, round(min(width, height) * 0.024))

    for index, change in enumerate(spec.changes, start=1):
        x1 = round(change.x * width)
        y1 = round(change.y * height)
        x2 = round((change.x + change.w) * width)
        y2 = round((change.y + change.h) * height)
        draw.rectangle(
            (x1, y1, x2, y2),
            fill=(16, 185, 129, 32),
            outline=(5, 120, 87, 255),
            width=line_width,
        )
        cx = max(badge_radius, min(width - badge_radius, x1 + badge_radius))
        cy = max(badge_radius, min(height - badge_radius, y1 + badge_radius))
        draw.ellipse(
            (cx - badge_radius, cy - badge_radius, cx + badge_radius, cy + badge_radius),
            fill=(5, 82, 70, 245),
        )
        number = str(index)
        bbox = draw.textbbox((0, 0), number)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.text(
            (cx - text_width / 2, cy - text_height / 2 - bbox[1]),
            number,
            fill=(255, 255, 255, 255),
        )
    return revised


def _rl_image(data: bytes, max_width: float, max_height: float) -> RLImage:
    with PILImage.open(BytesIO(data)) as image:
        width, height = image.size
    scale = min(max_width / width, max_height / height)
    stream = BytesIO(data)
    stream.seek(0)
    return RLImage(stream, width=width * scale, height=height * scale)


def render_sketch_before_after_pdf(
    *,
    question: str,
    answer: str,
    original_image: bytes,
    spec: SketchFixSpec | None,
) -> bytes:
    """Render a compressed educational sketch review with before/after visuals."""

    original = _prepared_image(original_image)
    revised = _annotated_revision(original, spec)
    before_jpeg = _jpeg_bytes(original)
    after_jpeg = _jpeg_bytes(revised)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=11 * mm,
        leftMargin=11 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title="BuildWise AI - Sketch Before and After Review",
        author="BuildWise AI",
        pageCompression=1,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SketchTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=21,
        alignment=TA_CENTER,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "SketchSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#505A66"),
    )
    heading_style = ParagraphStyle(
        "SketchHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        spaceBefore=5,
        spaceAfter=3,
    )
    body_style = ParagraphStyle(
        "SketchBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.7,
        leading=12,
        spaceAfter=2,
    )
    small_style = ParagraphStyle(
        "SketchSmall",
        parent=body_style,
        fontSize=7.7,
        leading=10,
        textColor=colors.HexColor("#59636F"),
    )

    story: list[object] = [
        Paragraph("BuildWise AI - Sketch Review", title_style),
        Paragraph(
            "Before / after learning view with a conceptual correction overlay",
            subtitle_style,
        ),
        Spacer(1, 3 * mm),
    ]

    if question and not question.lower().startswith("image upload"):
        story.extend(
            [
                Paragraph("Student request", heading_style),
                Paragraph(escape(_safe_text(question)), body_style),
                Spacer(1, 2 * mm),
            ]
        )

    before = _rl_image(before_jpeg, 128 * mm, 86 * mm)
    after = _rl_image(after_jpeg, 128 * mm, 86 * mm)
    visual_table = Table(
        [
            [
                Paragraph("<b>BEFORE - ORIGINAL SKETCH</b>", body_style),
                Paragraph("<b>AFTER - CORRECTED CONCEPT OVERLAY</b>", body_style),
            ],
            [before, after],
        ],
        colWidths=[134 * mm, 134 * mm],
    )
    visual_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([visual_table, Spacer(1, 3 * mm)])

    story.append(Paragraph("What BuildWise would fix", heading_style))
    if spec is not None and spec.summary:
        story.append(Paragraph(escape(_safe_text(spec.summary)), body_style))
    if spec is not None and spec.changes:
        for index, change in enumerate(spec.changes, start=1):
            story.append(
                Paragraph(
                    f"<b>{index}. {escape(_safe_text(change.label))}</b> - "
                    f"{escape(_safe_text(change.action))}",
                    body_style,
                )
            )
    else:
        story.append(
            Paragraph(
                "No reliable localized geometry correction could be placed automatically. "
                "Use the written review below rather than inventing dimensions or hidden details.",
                body_style,
            )
        )

    story.extend([Spacer(1, 2 * mm), Paragraph("Explanation", heading_style)])
    for raw_line in answer.splitlines():
        line = _safe_text(raw_line)
        if line:
            story.append(Paragraph(escape(line), body_style))

    story.extend(
        [
            Spacer(1, 3 * mm),
            Paragraph(
                "Important: The AFTER view is a conceptual learning overlay, not a construction drawing, "
                "code approval, structural design, dimensioned working drawing, or permit document. "
                "BuildWise does not invent unseen geometry or measurements. Confirm design changes with "
                "the appropriate qualified local professional.",
                small_style,
            ),
        ]
    )
    document.build(story)
    return buffer.getvalue()

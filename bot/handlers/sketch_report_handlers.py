"""Private, answer-bound sketch PDF downloads without another model request."""
import asyncio
import logging
from html import escape
from io import BytesIO

from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from telegram.ext import CallbackQueryHandler

from bot.formatting import clean_sketch_text, DISCLAIMERS
from bot.domain import Mode
from bot.student_solution_pdf import _pdf_safe_text


def render_sketch_report(answer: str, image_bytes: bytes | None = None) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph("BuildWise Sketch Review", styles["Title"]),
             Paragraph(escape(DISCLAIMERS[Mode.SKETCH]), styles["BodyText"]), Spacer(1, 16)]
    if image_bytes:
        figure = Image(BytesIO(image_bytes))
        figure._restrictSize(450, 420)
        story.extend([Paragraph("Original submitted sketch", styles["Heading2"]), figure, Spacer(1, 16)])
    for paragraph in clean_sketch_text(answer).split("\n\n"):
        story.append(Paragraph(escape(_pdf_safe_text(paragraph)).replace("\n", "<br/>"), styles["BodyText"]))
        story.append(Spacer(1, 8))
    SimpleDocTemplate(output, title="BuildWise Sketch Review", author="BuildWise AI").build(story)
    return output.getvalue()


class SketchReportHandlers:
    def register(self, application):
        application.add_handler(CallbackQueryHandler(self.download, pattern=r"^sketchpdf:[0-9a-f-]{36}$"))

    async def download(self, update, context):
        query = update.callback_query
        if not query or not query.message:
            return
        if not update.effective_chat or update.effective_chat.type != "private":
            await query.answer("Open your private BuildWise chat.", show_alert=True)
            return
        await query.answer()
        key = query.data.partition(":")[2]
        record = context.user_data.get("sketch_reports", {}).get(key)
        if not record:
            await query.message.reply_text("This report has expired. Please submit the sketch again.")
            return
        try:
            data = await asyncio.to_thread(render_sketch_report, record["answer"], record.get("image"))
        except Exception as exc:
            logging.getLogger(__name__).warning("Sketch PDF rendering failed: %s", type(exc).__name__)
            await query.message.reply_text("The PDF could not be prepared. Please try again or submit a smaller image.")
            return
        await query.message.reply_document(document=BytesIO(data), filename="BuildWise-sketch-review.pdf")

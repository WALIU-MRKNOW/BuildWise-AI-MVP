"""Student learning follow-ups that sit ahead of the generic callback handler."""

from __future__ import annotations

from io import BytesIO

from PIL import UnidentifiedImageError
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.domain import Mode
from bot.sketch_experience import SketchFixSpec
from bot.student_sketch_pdf import render_sketch_before_after_pdf
from bot.student_solution_pdf import render_student_solution_pdf
from bot.study_experience import StudyRequestKind, infer_study_answer_kind
from bot.services.supabase_service import SupabaseError, SupabaseService


class StudentLearningHandlers:
    """Handle student-specific document actions without changing Pro flows."""

    def __init__(self, database: SupabaseService) -> None:
        self._database = database

    def register(self, application: Application) -> None:
        # Register before BotHandlers' catch-all CallbackQueryHandler so these
        # specific actions are consumed here instead of falling through.
        application.add_handler(
            CallbackQueryHandler(self.solution_pdf, pattern=r"^followup:pdf$")
        )
        application.add_handler(
            CallbackQueryHandler(self.sketch_pdf, pattern=r"^followup:sketch_pdf$")
        )

    async def solution_pdf(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await query.answer()

        raw_mode = context.user_data.get("last_mode")
        question = str(context.user_data.get("last_question", "")).strip()
        answer = str(context.user_data.get("last_answer", "")).strip()
        if raw_mode != Mode.STUDY.value or not question or not answer:
            await query.message.reply_text(
                "Ask a Study & Solve question first, then use the PDF button on that answer."
            )
            return

        pdf_bytes = render_student_solution_pdf(question, answer)
        kind, _ = infer_study_answer_kind(answer)
        filename = (
            "BuildWise_Worked_Solution.pdf"
            if kind is StudyRequestKind.CALCULATION
            else "BuildWise_Study_Notes.pdf"
        )
        document = BytesIO(pdf_bytes)
        document.name = filename
        await query.message.reply_document(
            document=document,
            filename=filename,
            caption=(
                "📄 Your BuildWise worked solution is ready."
                if kind is StudyRequestKind.CALCULATION
                else "📄 Your BuildWise study notes are ready."
            ),
        )

    async def sketch_pdf(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await query.answer()

        raw_mode = context.user_data.get("last_mode")
        question = str(context.user_data.get("last_question", "")).strip()
        answer = str(context.user_data.get("last_answer", "")).strip()
        if raw_mode != Mode.SKETCH.value or not answer:
            await query.message.reply_text(
                "Analyze a sketch or floor plan first, then use the Before/After PDF button."
            )
            return

        original = context.user_data.get("last_sketch_image_bytes")
        if not isinstance(original, (bytes, bytearray)):
            reference = context.user_data.get("last_file_reference")
            if reference:
                try:
                    original = await self._database.download_file(str(reference))
                except SupabaseError:
                    original = None

        if not isinstance(original, (bytes, bytearray)) or not original:
            await query.message.reply_text(
                "I no longer have the sketch image for this review. Send the sketch again and tap Before/After PDF on the new result."
            )
            return

        spec_value = context.user_data.get("last_sketch_fix_spec")
        spec = spec_value if isinstance(spec_value, SketchFixSpec) else None
        try:
            pdf_bytes = render_sketch_before_after_pdf(
                question=question,
                answer=answer,
                original_image=bytes(original),
                spec=spec,
            )
        except (OSError, ValueError, UnidentifiedImageError):
            await query.message.reply_text(
                "I could not build the sketch PDF from that image. Please resend a clear PNG or JPG sketch."
            )
            return

        filename = "BuildWise_Sketch_Before_After.pdf"
        document = BytesIO(pdf_bytes)
        document.name = filename
        await query.message.reply_document(
            document=document,
            filename=filename,
            caption=(
                "🛠 Your compressed BuildWise before/after sketch review is ready. "
                "The AFTER view is a conceptual correction overlay, not a construction or permit drawing."
            ),
        )

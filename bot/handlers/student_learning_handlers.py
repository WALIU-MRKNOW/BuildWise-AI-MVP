"""Student learning follow-ups that sit ahead of the generic callback handler."""

from __future__ import annotations

from io import BytesIO

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.domain import Mode
from bot.student_solution_pdf import render_student_solution_pdf
from bot.study_experience import StudyRequestKind, strip_learning_marker


class StudentLearningHandlers:
    """Handle student-specific actions without changing Pro or analysis flows."""

    def register(self, application: Application) -> None:
        # Register before BotHandlers' catch-all CallbackQueryHandler so this
        # specific action is consumed here instead of falling through.
        application.add_handler(
            CallbackQueryHandler(self.solution_pdf, pattern=r"^followup:pdf$")
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
        kind, _ = strip_learning_marker(answer)
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

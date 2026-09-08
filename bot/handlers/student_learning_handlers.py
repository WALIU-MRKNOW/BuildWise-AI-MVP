"""Student learning follow-ups that sit ahead of the generic callback handler."""

from __future__ import annotations

from io import BytesIO

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.domain import Mode
from bot.formatting import clean_student_math, split_for_telegram
from bot.student_solution_pdf import render_student_solution_pdf
from bot.study_experience import StudyRequestKind, infer_study_answer_kind


class StudentLearningHandlers:
    """Handle student-specific actions without changing Pro or analysis flows."""

    def register(self, application: Application) -> None:
        # Register before BotHandlers' catch-all CallbackQueryHandler so these
        # specific actions are consumed here instead of falling through.
        application.add_handler(
            CallbackQueryHandler(self.solution_pdf, pattern=r"^followup:pdf$")
        )
        application.add_handler(
            CallbackQueryHandler(self.copy_answer, pattern=r"^followup:copy$")
        )

    async def copy_answer(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return

        answer = str(context.user_data.get("last_answer", "")).strip()
        raw_mode = str(context.user_data.get("last_mode", "")).strip()
        if not answer:
            await query.answer("There is no recent answer to copy.", show_alert=True)
            return

        if raw_mode == Mode.STUDY.value:
            _, answer = infer_study_answer_kind(answer)
            answer = clean_student_math(answer)

        await query.answer("Copy-ready answer sent below.")
        for chunk in split_for_telegram(answer):
            await query.message.reply_text(chunk)

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

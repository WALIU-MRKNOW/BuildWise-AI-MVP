import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from bot.formatting import clean_sketch_text, sketch_preview
from bot.handlers.sketch_report_handlers import render_sketch_report
from bot.handlers.sketch_report_handlers import SketchReportHandlers
from bot.services.openai_service import OpenAIService, AIServiceError
from bot.config import Settings
from bot.domain import Mode, RequestPayload


class SketchReportTests(unittest.TestCase):
    def test_dimensions_and_commas_preserved(self):
        self.assertEqual(clean_sketch_text('### Bath\n**Size** 6\'-9" × 5\'-3", check door.'),
                         'Bath\nSize 6\'-9" × 5\'-3", check door.')

    def test_preview_is_bounded_and_complete(self):
        result = sketch_preview(('A complete review paragraph.\n\n' * 300))
        self.assertLess(len(result), 2000)
        self.assertIn('Get PDF', result)
        self.assertNotIn('**', result)

    def test_pdf_generation(self):
        data = render_sketch_report('## Review\n\nKeep the route clear.\n\nCheck the door swing.')
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertGreater(len(data), 1000)


class SketchDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_download_uses_own_answer(self):
        message = SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock())
        query = SimpleNamespace(message=message, answer=AsyncMock(), data='sketchpdf:abc')
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(type='private'))
        context = SimpleNamespace(user_data={'sketch_reports': {'abc': {'answer': 'Check the circulation.'}}})
        await SketchReportHandlers().download(update, context)
        message.reply_document.assert_awaited_once()
        self.assertTrue(message.reply_document.await_args.kwargs['document'].getvalue().startswith(b'%PDF-'))

    async def test_other_users_cannot_download(self):
        message = SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock())
        query = SimpleNamespace(message=message, answer=AsyncMock(), data='sketchpdf:abc')
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(type='private'))
        await SketchReportHandlers().download(update, SimpleNamespace(user_data={}))
        message.reply_document.assert_not_awaited()

    async def test_incomplete_sketch_is_rejected_and_budget_increased(self):
        settings = Settings(_env_file=None, TELEGRAM_BOT_TOKEN='123:test', OPENAI_API_KEY='test',
                            SUPABASE_URL='https://example.supabase.co', SUPABASE_SECRET_KEY='test')
        service = OpenAIService(settings)
        service._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(
            return_value=SimpleNamespace(status='incomplete', output_text='5. An'))))
        with self.assertRaises(AIServiceError):
            await service.answer(Mode.SKETCH, RequestPayload(text='Review this plan.'))
        self.assertEqual(service._client.responses.create.await_args.kwargs['max_output_tokens'], 4000)

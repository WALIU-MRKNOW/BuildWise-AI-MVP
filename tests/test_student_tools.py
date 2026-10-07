import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter, PdfReader

from bot.pdf_study import parse_study_caption, select_pages
from bot.handlers.student_tools_handlers import StudentToolsHandlers
from bot.services.student_tools_service import StudentToolsDatabase, recommendations
from tests.test_practice_persistence import settings, practice_session


class PdfBoundsTests(unittest.TestCase):
    def test_actual_selected_pages_only(self):
        writer = PdfWriter()
        for width in [100, 200, 300, 400]:
            writer.add_blank_page(width=width, height=500)
        source = BytesIO()
        writer.write(source)
        result = PdfReader(BytesIO(select_pages(source.getvalue(), 2, 3)))
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(float(result.pages[0].mediabox.width), 200)
        self.assertEqual(float(result.pages[1].mediabox.width), 300)

    def test_reject_invalid_ranges_and_files(self):
        for caption in ['/pdfstudy summary 0-2', '/pdfstudy terms 1-9', '/pdfstudy practice 3-2', '/pdfstudy unknown 1']:
            with self.subTest(caption=caption), self.assertRaises(ValueError):
                parse_study_caption(caption)
        with self.assertRaises(ValueError):
            select_pages(b'not a pdf', 1, 1)

    def test_encrypted_pdf_is_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt('test-only-password')
        source = BytesIO()
        writer.write(source)
        with self.assertRaisesRegex(ValueError, 'unencrypted'):
            select_pages(source.getvalue(), 1, 1)


class StudentToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_accuracy_write_is_idempotent_and_private(self):
        db = StudentToolsDatabase(settings())
        db._request = AsyncMock(return_value=[{'selected_index': 1}])
        await db.record_practice_answer(SimpleNamespace(id='u1', save_history=False), practice_session(), 1)
        call = db._request.await_args
        self.assertIn('ignore-duplicates', call.kwargs['headers']['Prefer'])
        self.assertEqual(call.kwargs['json']['user_id'], 'u1')
        for key in ['question', 'answer', 'explanation']:
            self.assertEqual(call.kwargs['json'][key], '')
        self.assertFalse(call.kwargs['json']['correct'])

    async def test_review_scoped_and_bounded(self):
        db = StudentToolsDatabase(settings())
        db._request = AsyncMock(return_value=[])
        await db.practice_review('owner')
        params = db._request.await_args.kwargs['params']
        self.assertEqual(params['user_id'], 'eq.owner')
        self.assertEqual(params['limit'], '200')
        self.assertTrue(params['created_at'].startswith('gte.'))

    async def test_stale_answer_does_not_write_or_change_score(self):
        handler = StudentToolsHandlers.__new__(StudentToolsHandlers)
        handler._settings = SimpleNamespace(student_tools_enabled=True)
        handler._database = SimpleNamespace(record_practice_answer=AsyncMock())
        context = SimpleNamespace(user_data={'practice_session': practice_session()})
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()))
        await handler._answer_practice_question(update, context, SimpleNamespace(id='u1'), 0, 'old', 0)
        handler._database.record_practice_answer.assert_not_awaited()
        self.assertEqual(context.user_data['practice_session']['correct'], 0)

    async def test_feedback_token_from_another_user_is_rejected(self):
        handler = StudentToolsHandlers.__new__(StudentToolsHandlers)
        handler._settings = SimpleNamespace(student_tools_enabled=True)
        handler._profile = AsyncMock(return_value=SimpleNamespace(id='u2'))
        handler._database = SimpleNamespace(save_feedback=AsyncMock())
        update = SimpleNamespace(callback_query=SimpleNamespace(data='student:feedback:helpful:other-token', answer=AsyncMock()),
                                 effective_chat=SimpleNamespace(type='private'),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        await handler.handle_callback(update, SimpleNamespace(user_data={}))
        handler._database.save_feedback.assert_not_awaited()

    async def test_notifications_allowlist_does_not_grant_analytics(self):
        handler = StudentToolsHandlers.__new__(StudentToolsHandlers)
        handler._admin_notify_ids = {99}
        handler._database = SimpleNamespace(_rpc=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=99), effective_message=SimpleNamespace(reply_text=AsyncMock()))
        with patch('bot.handlers.student_tools_handlers.validated_admin_telegram_ids', return_value=frozenset()):
            await handler.product_stats(update, SimpleNamespace())
        handler._database._rpc.assert_not_awaited()

    def test_recommendations_prioritize_accuracy(self):
        rows = [{'topic': 'concrete', 'correct': False}, {'topic': 'units', 'correct': True}]
        self.assertEqual(recommendations(rows)[0], ('concrete', 0, 1))


if __name__ == '__main__':
    unittest.main()

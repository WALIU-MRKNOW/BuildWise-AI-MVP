import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.domain import Mode, RequestPayload
from bot.services.openai_service import OpenAIService, AIServiceError


class PDFCompletionTests(unittest.IsolatedAsyncioTestCase):
    def service(self, responses):
        settings = Settings(_env_file=None, TELEGRAM_BOT_TOKEN='123:test', OPENAI_API_KEY='test',
                            SUPABASE_URL='https://example.supabase.co', SUPABASE_SECRET_KEY='test')
        service = OpenAIService(settings)
        service._client = NS(responses=NS(create=AsyncMock(side_effect=responses)))
        return service

    async def test_pdf_has_more_room_without_retry_when_complete(self):
        service = self.service([NS(status='completed', output_text='Complete explanation.')])
        answer = await service.answer(Mode.STUDY, RequestPayload(pdf_bytes=b'%PDF-test', filename='notes.pdf'))
        self.assertEqual(answer, 'Complete explanation.')
        self.assertEqual(service._client.responses.create.await_args.kwargs['max_output_tokens'], 4000)
        self.assertEqual(service._client.responses.create.await_count, 1)

    async def test_token_limit_retried_once_with_original_pdf(self):
        partial = NS(status='incomplete', output_text='5. An', incomplete_details=NS(reason='max_output_tokens'))
        service = self.service([partial, NS(status='completed', output_text='Finished review.')])
        answer = await service.answer(Mode.STUDY, RequestPayload(pdf_bytes=b'%PDF-test', filename='notes.pdf'))
        calls = service._client.responses.create.await_args_list
        self.assertEqual(answer, 'Finished review.')
        self.assertEqual([c.kwargs['max_output_tokens'] for c in calls], [4000, 8000])
        self.assertEqual(calls[0].kwargs['input'], calls[1].kwargs['input'])
        self.assertFalse(calls[1].kwargs['store'])

    async def test_second_incomplete_is_not_published(self):
        partial = NS(status='incomplete', output_text='Partial', incomplete_details=NS(reason='max_output_tokens'))
        service = self.service([partial, partial])
        with self.assertRaises(AIServiceError):
            await service.answer(Mode.STUDY, RequestPayload(text='Explain dampness.', pdf_bytes=b'%PDF-test'))
        self.assertEqual(service._client.responses.create.await_count, 2)

    async def test_filter_is_not_retried(self):
        service = self.service([NS(status='incomplete', output_text='', incomplete_details=NS(reason='content_filter'))])
        with self.assertRaises(AIServiceError):
            await service.answer(Mode.STUDY, RequestPayload(text='Question'))
        self.assertEqual(service._client.responses.create.await_count, 1)

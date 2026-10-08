import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from bot.config import Settings
from bot.handlers.payment_test_handlers import PaymentTestHandlers
from bot.payments_api import create_test_payment_router
from bot.services.payment_ledger import SandboxPayments
from bot.services.paystack_service import PaystackTestService, PaystackError

REFERENCE = 'bw-test-' + 'a' * 32
KEY = 'sk_test_syntheticfixture'


def settings(**overrides):
    return Settings(TELEGRAM_BOT_TOKEN='123:test-token', OPENAI_API_KEY='test-placeholder',
                    SUPABASE_URL='https://example.supabase.co', SUPABASE_SECRET_KEY='test-placeholder',
                    PAYSTACK_TEST_ENABLED=True, PAYSTACK_SECRET_KEY=KEY, **overrides)


def event(kind='charge.success', **data):
    return json.dumps({'event': kind, 'data': data or {'domain': 'test', 'reference': REFERENCE}}).encode()


def signature(raw):
    return hmac.new(KEY.encode(), raw, hashlib.sha512).hexdigest()


def transaction(**changes):
    return {'id': 77, 'domain': 'test', 'status': 'success', 'reference': REFERENCE,
            'currency': 'NGN', 'amount': 300000, **changes}


class PaymentLedgerTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, provider_data):
        calls = []
        async def transport(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={'status': True, 'data': provider_data(request.url.path)})
        provider = PaystackTestService(settings(), transport=httpx.MockTransport(transport))
        db = SimpleNamespace(_request=AsyncMock(), _rpc=AsyncMock(return_value='paid'))
        db._request.return_value = [{'reference': REFERENCE, 'user_id': 'owner', 'amount_kobo': 300000}]
        return SandboxPayments(db, provider), db, calls

    async def test_signed_charge_requires_independent_exact_verification(self):
        flow, db, calls = self.setup_flow(lambda _: transaction())
        raw = event()
        result = await flow.handle_event(raw, signature(raw))
        self.assertEqual(result, 'paid')
        self.assertEqual(calls, [f'/transaction/verify/{REFERENCE}'])
        receipt = db._request.await_args_list[1]
        self.assertEqual(receipt.args[1], '/rest/v1/paystack_test_events')
        self.assertEqual(receipt.kwargs['json']['digest'], hashlib.sha256(raw).hexdigest())
        self.assertNotIn('raw', receipt.kwargs['json'])
        self.assertEqual(db._rpc.await_args.args[1]['p_transaction'], 77)

    async def test_wrong_amount_or_live_verification_never_fulfills(self):
        for changes in [{'amount': 100}, {'domain': 'live'}, {'reference': 'wrong'}]:
            flow, db, _ = self.setup_flow(lambda _, c=changes: transaction(**c))
            raw = event()
            with self.assertRaises(PaystackError):
                await flow.handle_event(raw, signature(raw))
            db._rpc.assert_not_awaited()

    async def test_unsigned_event_has_no_database_or_provider_effect(self):
        flow, db, calls = self.setup_flow(lambda _: transaction())
        with self.assertRaises(PaystackError):
            await flow.handle_event(event(), '0'*128)
        db._request.assert_not_awaited()
        self.assertEqual(calls, [])

    async def test_unknown_reference_never_creates_intent(self):
        flow, db, calls = self.setup_flow(lambda _: transaction())
        db._request.return_value = []
        raw = event()
        self.assertEqual(await flow.handle_event(raw, signature(raw)), 'unknown')
        self.assertEqual(db._request.await_count, 1)
        self.assertEqual(calls, [])

    async def test_full_refund_reverified_before_revocation(self):
        def data(path):
            return {'id': 88, 'domain': 'test', 'transaction': 77, 'currency': 'NGN', 'amount': 300000, 'status': 'processed'} if path == '/refund/88' else transaction(status='reversed')
        flow, db, calls = self.setup_flow(data)
        raw = event('refund.processed', id=88)
        await flow.handle_event(raw, signature(raw))
        self.assertEqual(calls, ['/refund/88', '/transaction/77'])
        self.assertEqual(db._rpc.await_args.args[1]['p_action'], 'refunded')

    async def test_partial_refund_or_dispute_remains_on_hold(self):
        for kind in ['refund.processed', 'charge.dispute.resolve']:
            def data(path):
                return transaction() if path.startswith('/transaction/') else {'id': 88, 'domain': 'test', 'transaction': 77, 'currency': 'NGN', 'amount': 10000, 'status': 'processed'}
            flow, db, _ = self.setup_flow(data)
            raw = event(kind, id=88)
            await flow.handle_event(raw, signature(raw))
            self.assertEqual(db._rpc.await_args.args[1]['p_action'], 'suspended')

    async def test_manual_check_is_owner_scoped(self):
        flow, db, calls = self.setup_flow(lambda _: transaction())
        db._request.return_value = []
        with self.assertRaises(PaystackError):
            await flow.reconcile(REFERENCE, 'different-owner')
        self.assertEqual(db._request.await_args.kwargs['params']['user_id'], 'eq.different-owner')
        self.assertEqual(calls, [])

    async def test_intent_persisted_before_checkout_and_no_ambiguous_retry(self):
        flow, db, calls = self.setup_flow(lambda _: {})
        provider = SimpleNamespace(new_intent=lambda: SimpleNamespace(reference=REFERENCE, amount_kobo=300000),
                                   initialize_checkout=AsyncMock(side_effect=PaystackError('timeout')))
        flow.provider = provider
        with self.assertRaises(PaystackError):
            await flow.checkout('owner', 'update-1', 'test@example.com')
        self.assertEqual(db._request.await_args.args[0], 'POST')
        self.assertEqual(db._request.await_args.args[1], '/rest/v1/paystack_test_intents')
        db._request.side_effect = [[], [{'reference': REFERENCE}]]
        with self.assertRaises(PaystackError):
            await flow.checkout('owner', 'update-1', 'test@example.com')
        self.assertEqual(provider.initialize_checkout.await_count, 1)

    async def test_http_webhook_success_and_db_failure_response(self):
        flow, db, _ = self.setup_flow(lambda _: transaction())
        app = FastAPI()
        with patch('bot.payments_api.PaystackTestService', return_value=flow.provider):
            app.include_router(create_test_payment_router(settings(), db))
        raw = event()
        headers = {'content-type': 'application/json', 'x-paystack-signature': signature(raw)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
            response = await client.post('/payments/paystack/test/webhook', content=raw, headers=headers)
            self.assertEqual(response.status_code, 200)
            from bot.services.supabase_service import SupabaseError
            db._rpc.side_effect = SupabaseError('unavailable')
            response = await client.post('/payments/paystack/test/webhook', content=raw, headers=headers)
            self.assertEqual(response.status_code, 503)

    async def test_http_bad_signature_oversize_and_disabled(self):
        flow, db, calls = self.setup_flow(lambda _: transaction())
        app = FastAPI()
        with patch('bot.payments_api.PaystackTestService', return_value=flow.provider):
            app.include_router(create_test_payment_router(settings(), db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
            bad = await client.post('/payments/paystack/test/webhook', json={'event': 'charge.success'})
            self.assertEqual(bad.status_code, 403)
            huge = await client.post('/payments/paystack/test/webhook', content=b'x'*65537, headers={'content-type': 'application/json'})
            self.assertEqual(huge.status_code, 413)
        self.assertEqual(calls, [])
        disabled = settings()
        disabled.paystack_test_enabled = False
        app = FastAPI()
        app.include_router(create_test_payment_router(disabled, db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
            self.assertEqual((await client.post('/payments/paystack/test/webhook')).status_code, 404)

    def test_live_key_rejected_at_router_construction(self):
        from pydantic import SecretStr
        config = settings()
        config.paystack_secret_key = SecretStr('sk_live_neveraccepted')
        with self.assertRaises(PaystackError):
            create_test_payment_router(config, SimpleNamespace())

    async def test_checkout_requires_explicit_tester_and_enabled_flag(self):
        for enabled, allowed in [(True, ''), (False, '123')]:
            config = settings(PAYSTACK_TEST_ALLOWED_TELEGRAM_IDS=allowed)
            config.paystack_test_enabled = enabled
            students = SimpleNamespace(_profile=AsyncMock())
            handler = PaymentTestHandlers(config, SimpleNamespace(), students)
            update = SimpleNamespace(effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(reply_text=AsyncMock(), text='/premiumtest'))
            await handler.command(update, SimpleNamespace(args=[]))
            students._profile.assert_not_awaited()
            self.assertIn('restricted', update.effective_message.reply_text.await_args.args[0])

    def test_test_commands_are_private_and_absent_when_disabled(self):
        config = settings(PAYSTACK_TEST_ALLOWED_TELEGRAM_IDS='123')
        from unittest.mock import Mock
        application = SimpleNamespace(add_handler=Mock())
        handler = PaymentTestHandlers(config, SimpleNamespace(), SimpleNamespace())
        handler.register(application)
        self.assertEqual(application.add_handler.call_count, 3)
        for call in application.add_handler.call_args_list:
            self.assertIn('PRIVATE', str(call.args[0].filters))
        config.paystack_test_enabled = False
        application.add_handler.reset_mock()
        handler.register(application)
        application.add_handler.assert_not_called()

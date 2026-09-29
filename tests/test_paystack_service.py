import hashlib
import hmac
import json
import unittest

import httpx

from bot.api import RuntimeHealth, create_api
from bot.config import Settings
from bot.services.paystack_service import PaystackError, PaystackTestService, TestPaymentIntent
from bot.webhook_runtime import create_webhook_api


TEST_KEY = "sk_test_fixtureonly"


def settings(**overrides):
    return Settings(_env_file=None, **{
        "TELEGRAM_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
        **overrides,
    })


def sandbox(**overrides):
    return settings(PAYSTACK_TEST_ENABLED=True, PAYSTACK_SECRET_KEY=TEST_KEY, **overrides)


def signed(body):
    return hmac.new(TEST_KEY.encode(), body, hashlib.sha512).hexdigest()


class PaystackConfigTests(unittest.TestCase):
    def test_disabled_by_default_and_premium_stays_disabled(self):
        config = settings()
        self.assertFalse(config.paystack_test_enabled)
        self.assertIsNone(config.paystack_secret_key)
        self.assertFalse(sandbox().student_premium_enabled)
        with self.assertRaises(PaystackError):
            PaystackTestService(config).new_intent()

    def test_live_public_missing_and_malformed_keys_are_rejected(self):
        for key in (None, "", "sk_live_fixture", "pk_test_fixture", "sk_test_", " sk_test_fixture", "sk_test_x\n"):
            with self.subTest(key=key), self.assertRaises(PaystackError):
                PaystackTestService(settings(PAYSTACK_TEST_ENABLED=True, PAYSTACK_SECRET_KEY=key)).new_intent()

    def test_key_is_redacted_from_settings(self):
        self.assertNotIn(TEST_KEY, repr(sandbox()))

    def test_intent_price_is_integer_kobo_snapshot_with_unique_reference(self):
        config = sandbox()
        service = PaystackTestService(config)
        first, second = service.new_intent(), service.new_intent()
        self.assertEqual(first.amount_kobo, 300000)
        self.assertNotEqual(first.reference, second.reference)
        config.student_premium_price_ngn = 3500
        self.assertEqual(first.amount_kobo, 300000)
        self.assertEqual(service.new_intent().amount_kobo, 350000)

    def test_invalid_intent_cannot_supply_a_url_path_or_noninteger_amount(self):
        reference = "bw-test-" + "a" * 32
        for amount in (True, 300000.0, "300000", 0, -1, 10000001):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                TestPaymentIntent(reference, amount)
        for value in ("../verify", "abc?secret=1", "https://evil.test", None):
            with self.subTest(reference=value), self.assertRaises(ValueError):
                TestPaymentIntent(value, 300000)

    def test_sandbox_flag_does_not_mount_payment_routes(self):
        for config in (settings(), sandbox()):
            api = create_api(RuntimeHealth(), config)
            paths = set(api.openapi()["paths"])
            self.assertFalse(any("paystack" in path or "premium" in path for path in paths))

    def test_production_webhook_runtime_has_no_payment_routes(self):
        config = sandbox(
            RUNTIME_MODE="webhook",
            TELEGRAM_WEBHOOK_SECRET="telegram-webhook-secret-123456",
            TELEGRAM_WEBHOOK_BASE_URL="https://buildwise.example.test",
            INTERNAL_CRON_SECRET="internal-cron-secret-123456789",
        )
        paths = set(create_webhook_api(config).openapi()["paths"])
        self.assertIn("/telegram/webhook", paths)
        self.assertFalse(any("paystack" in path or "premium" in path for path in paths))


class PaystackRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = sandbox()
        self.intent = PaystackTestService(self.config).new_intent()
        self.requests = []

    def service(self, data=None, *, status=200, raw=None):
        def handle(request):
            self.requests.append(request)
            if raw is not None:
                return httpx.Response(status, content=raw)
            return httpx.Response(status, json={"status": True, "data": data})
        return PaystackTestService(self.config, transport=httpx.MockTransport(handle))

    def payment(self, **overrides):
        return {
            "id": 42, "domain": "test", "status": "success",
            "reference": self.intent.reference, "amount": 300000, "currency": "NGN",
            **overrides,
        }

    async def test_checkout_sends_price_snapshot_and_no_subscription_or_identity(self):
        service = self.service({"reference": self.intent.reference, "authorization_url": "https://checkout.paystack.com/test-code"})
        checkout = await service.initialize_checkout(self.intent, email="tester@example.test")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://api.paystack.co/transaction/initialize")
        self.assertEqual(request.headers["Authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(json.loads(request.content), {
            "reference": self.intent.reference, "email": "tester@example.test",
            "amount": "300000", "currency": "NGN",
        })
        self.assertEqual(checkout.reference, self.intent.reference)

    async def test_checkout_rejects_redirects_and_mismatched_references(self):
        for url in ("http://checkout.paystack.com/x", "https://evil.test/x", "https://checkout.paystack.com.evil.test/x", "https://user@checkout.paystack.com/x", "https://checkout.paystack.com/", "https://checkout.paystack.com/x\n"):
            with self.subTest(url=url), self.assertRaises(PaystackError):
                await self.service({"reference": self.intent.reference, "authorization_url": url}).initialize_checkout(self.intent, email="tester@example.test")
        with self.assertRaises(PaystackError):
            await self.service({"reference": "different", "authorization_url": "https://checkout.paystack.com/x"}).initialize_checkout(self.intent, email="tester@example.test")
        with self.assertRaises(PaystackError):
            await self.service(status=302).initialize_checkout(self.intent, email="tester@example.test")

    async def test_invalid_email_and_disabled_service_make_no_network_calls(self):
        service = self.service()
        for email in ("", "invalid", "a\nb@example.test", "x" * 255):
            with self.assertRaises(ValueError):
                await service.initialize_checkout(self.intent, email=email)
        self.config.paystack_test_enabled = False
        with self.assertRaises(PaystackError):
            await service.initialize_checkout(self.intent, email="tester@example.test")
        with self.assertRaises(PaystackError):
            await service.verify_payment(self.intent)
        self.assertEqual(self.requests, [])

    async def test_success_requires_independent_verification(self):
        evidence = await self.service(self.payment()).verify_payment(self.intent)
        self.assertEqual(evidence.transaction_id, 42)
        self.assertEqual(evidence.amount_kobo, 300000)
        self.assertEqual(self.requests[0].method, "GET")
        self.assertEqual(self.requests[0].url.path, f"/transaction/verify/{self.intent.reference}")

    async def test_verification_rejects_failed_refunded_live_and_mismatched_payments(self):
        invalid = [
            {"domain": "live"}, {"domain": None}, {"currency": "USD"},
            {"reference": "another-user"}, {"amount": 1}, {"amount": "300000"},
            {"amount": 300000.0}, {"amount": True}, {"id": True}, {"id": 0},
            *({"status": status} for status in ("failed", "pending", "abandoned", "reversed", "refunded", None)),
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(PaystackError):
                await self.service(self.payment(**fields)).verify_payment(self.intent)

    async def test_bad_envelopes_and_http_errors_are_sanitized(self):
        for raw in (b"not json", b"[]", b'null', b'{"status":1,"data":{}}', b'{"status":false,"message":"private"}', b'{"status":true,"data":[]}'):
            with self.subTest(raw=raw), self.assertRaises(PaystackError) as caught:
                await self.service(raw=raw).verify_payment(self.intent)
            self.assertNotIn("private", str(caught.exception))
        for status in (401, 429, 500):
            with self.subTest(status=status), self.assertRaises(PaystackError) as caught:
                await self.service(status=status, raw=TEST_KEY.encode()).verify_payment(self.intent)
            self.assertNotIn(TEST_KEY, str(caught.exception))

    async def test_timeout_and_network_errors_never_retry_initialization(self):
        for error in (httpx.ReadTimeout, httpx.ConnectError):
            calls = []
            def fail(request):
                calls.append(request)
                raise error("private provider detail", request=request)
            service = PaystackTestService(self.config, transport=httpx.MockTransport(fail))
            with self.assertRaisesRegex(PaystackError, "reconcile") as caught:
                await service.initialize_checkout(self.intent, email="tester@example.test")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private", str(caught.exception))


class PaystackSignatureTests(unittest.TestCase):
    def setUp(self):
        self.service = PaystackTestService(sandbox())
        self.intent = self.service.new_intent()
        self.body = json.dumps({"event": "charge.success", "data": {
            "reference": self.intent.reference, "domain": "test",
        }}).encode()

    def test_exact_raw_body_signature_required_before_parsing(self):
        self.assertEqual(self.service.webhook_reference(self.body, signed(self.body)), self.intent.reference)
        for signature in ("", "0" * 128, "é" * 128, None):
            with self.assertRaises(PaystackError):
                self.service.webhook_reference(self.body, signature)
        with self.assertRaises(PaystackError):
            self.service.webhook_reference(self.body + b" ", signed(self.body))

    def test_malformed_oversized_and_live_events_are_rejected(self):
        for body in (b"invalid", b"[]", b"\xff", b" " * 65537,
                     self.body.replace(b'"test"', b'"live"'),
                     b'{"event":"charge.success","data":null}',
                     b'{"event":"charge.success","data":{"domain":"test","reference":"../bad"}}'):
            with self.subTest(body=body[:100]), self.assertRaises(PaystackError):
                self.service.webhook_reference(body, signed(body))

    def test_unrelated_and_refund_events_do_not_return_success_reference(self):
        for event in ("refund.processed", "charge.failed", "subscription.create"):
            body = json.dumps({"event": event}).encode()
            self.assertIsNone(self.service.webhook_reference(body, signed(body)))

    def test_replays_only_return_lookup_reference_never_grant_access(self):
        for _ in range(2):
            self.assertEqual(self.service.webhook_reference(self.body, signed(self.body)), self.intent.reference)
        self.assertFalse(self.service._settings.student_premium_enabled)

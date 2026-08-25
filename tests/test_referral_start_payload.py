from types import SimpleNamespace
import unittest

from bot.handlers.referral_handlers import referral_start_payload


class ReferralStartPayloadTests(unittest.TestCase):
    def test_context_args_payload(self) -> None:
        update = SimpleNamespace(effective_message=SimpleNamespace(text="/start ignored"))
        context = SimpleNamespace(args=["ABCDEF0123456789ABCDEF0123456789"])
        code, supplied = referral_start_payload(update, context)
        self.assertTrue(supplied)
        self.assertEqual(code, "abcdef0123456789abcdef0123456789")

    def test_raw_message_fallback(self) -> None:
        update = SimpleNamespace(
            effective_message=SimpleNamespace(
                text="/start@BuildWiseBot abcdef0123456789abcdef0123456789"
            )
        )
        context = SimpleNamespace(args=[])
        code, supplied = referral_start_payload(update, context)
        self.assertTrue(supplied)
        self.assertEqual(code, "abcdef0123456789abcdef0123456789")

    def test_invalid_payload_is_reported_but_not_used(self) -> None:
        update = SimpleNamespace(effective_message=SimpleNamespace(text="/start not-a-code"))
        context = SimpleNamespace(args=[])
        code, supplied = referral_start_payload(update, context)
        self.assertTrue(supplied)
        self.assertIsNone(code)

    def test_plain_start_has_no_payload(self) -> None:
        update = SimpleNamespace(effective_message=SimpleNamespace(text="/start"))
        context = SimpleNamespace(args=[])
        code, supplied = referral_start_payload(update, context)
        self.assertFalse(supplied)
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()

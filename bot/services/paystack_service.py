"""Internal test-only Paystack boundary. No routes, storage or entitlements.

An eventual payment coordinator must persist each intent before initialization,
load it by reference for verification, and atomically deduplicate fulfillment.
Never construct the expected intent from callback/webhook data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from bot.config import Settings

_REFERENCE = re.compile(r"bw-test-[a-f0-9]{32}")
_MAX_WEBHOOK_BYTES = 64 * 1024


class PaystackError(RuntimeError):
    """Sanitized error: never includes provider bodies, credentials or emails."""


@dataclass(frozen=True, slots=True)
class TestPaymentIntent:
    """Server-owned price snapshot; deliberately contains no billing duration."""

    reference: str
    amount_kobo: int

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not _REFERENCE.fullmatch(self.reference):
            raise ValueError("Invalid test payment reference")
        if type(self.amount_kobo) is not int or not 10000 <= self.amount_kobo <= 10000000:
            raise ValueError("Invalid test payment amount")


@dataclass(frozen=True, slots=True)
class TestCheckout:
    reference: str
    authorization_url: str


@dataclass(frozen=True, slots=True)
class VerifiedTestPayment:
    """Verification evidence only. This object does not grant paid access."""

    reference: str
    transaction_id: int
    amount_kobo: int


class PaystackTestService:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _test_key(self) -> str:
        if not self._settings.paystack_test_enabled:
            raise PaystackError("Paystack sandbox is disabled")
        secret = self._settings.paystack_secret_key
        key = secret.get_secret_value() if secret else ""
        if not re.fullmatch(r"sk_test_[A-Za-z0-9]+", key):
            raise PaystackError("Paystack sandbox requires a test secret key")
        return key

    def new_intent(self) -> TestPaymentIntent:
        self._test_key()
        return TestPaymentIntent(
            reference=f"bw-test-{uuid4().hex}",
            amount_kobo=self._settings.student_premium_price_ngn * 100,
        )

    async def _request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        key = self._test_key()
        # Fixed origin, no redirects, no automatic retry of ambiguous checkout POSTs.
        async with httpx.AsyncClient(
            base_url="https://api.paystack.co",
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(method, path, json=payload)
            except httpx.TimeoutException:
                raise PaystackError(
                    "Paystack request timed out; reconcile the existing reference"
                ) from None
            except httpx.HTTPError:
                raise PaystackError(
                    "Paystack is unavailable; reconcile the existing reference"
                ) from None
        if response.status_code != 200:
            raise PaystackError("Paystack request was not accepted")
        try:
            result = response.json()
        except (ValueError, UnicodeError):
            raise PaystackError("Invalid Paystack response") from None
        if not isinstance(result, dict) or result.get("status") is not True:
            raise PaystackError("Paystack request was not successful")
        data = result.get("data")
        if not isinstance(data, dict):
            raise PaystackError("Invalid Paystack response")
        return data

    async def initialize_checkout(
        self, intent: TestPaymentIntent, *, email: str
    ) -> TestCheckout:
        """Internal sandbox operation; caller must persist intent before calling."""
        self._test_key()
        if (
            not isinstance(email, str)
            or len(email) > 254
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        ):
            raise ValueError("A valid tester email is required")
        data = await self._request(
            "POST", "/transaction/initialize",
            payload={
                "email": email,
                "amount": str(intent.amount_kobo),
                "currency": "NGN",
                "reference": intent.reference,
                # No subscription plan, Telegram identity, or recurring authorization.
            },
        )
        url = data.get("authorization_url")
        if data.get("reference") != intent.reference or not isinstance(url, str):
            raise PaystackError("Invalid Paystack checkout response")
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https" and parsed.netloc == "checkout.paystack.com"
                and bool(parsed.path.strip("/")) and not parsed.fragment
                and not any(char.isspace() or ord(char) < 32 for char in url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise PaystackError("Invalid Paystack checkout URL")
        return TestCheckout(intent.reference, url)

    async def verify_payment(self, intent: TestPaymentIntent) -> VerifiedTestPayment:
        """Recheck with Paystack; callback parameters alone are never payment proof."""
        data = await self._request("GET", f"/transaction/verify/{intent.reference}")
        transaction_id = data.get("id")
        if (
            data.get("domain") != "test"
            or data.get("status") != "success"
            or data.get("reference") != intent.reference
            or data.get("currency") != "NGN"
            or type(data.get("amount")) is not int
            or data.get("amount") != intent.amount_kobo
            or type(transaction_id) is not int
            or transaction_id <= 0
        ):
            raise PaystackError("Test payment verification did not match the expected payment")
        return VerifiedTestPayment(intent.reference, transaction_id, intent.amount_kobo)

    def webhook_reference(self, raw_body: bytes, signature: str) -> str | None:
        """Authenticate raw bytes, then extract a test charge reference for lookup.

        Not a webhook consumer: does not acknowledge, persist, or fulfill events.
        A signed event can be replayed; the future ledger must handle duplicates.
        """
        key = self._test_key()
        if len(raw_body) > _MAX_WEBHOOK_BYTES:
            raise PaystackError("Paystack event is too large")
        if not isinstance(signature, str) or not re.fullmatch(r"[a-fA-F0-9]{128}", signature):
            raise PaystackError("Invalid Paystack signature")
        expected = hmac.new(key.encode(), raw_body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            raise PaystackError("Invalid Paystack signature")
        try:
            event = json.loads(raw_body)
        except (ValueError, UnicodeError, RecursionError):
            raise PaystackError("Invalid Paystack event") from None
        if not isinstance(event, dict):
            raise PaystackError("Invalid Paystack event")
        if event.get("event") != "charge.success":
            return None
        data = event.get("data")
        if not isinstance(data, dict) or data.get("domain") != "test":
            raise PaystackError("Expected a test Paystack event")
        reference = data.get("reference")
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise PaystackError("Invalid test payment reference")
        return reference

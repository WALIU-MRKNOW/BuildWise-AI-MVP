"""Bounded authenticated Paystack sandbox webhook; absent while disabled."""
from fastapi import APIRouter, HTTPException, Request, Response

from bot.security import read_limited_body, validate_json_request
from bot.services.paystack_service import PaystackTestService, PaystackError
from bot.services.payment_ledger import SandboxPayments
from bot.services.supabase_service import SupabaseError


def create_test_payment_router(settings, database):
    router = APIRouter()
    if not settings.paystack_test_enabled:
        return router
    provider = PaystackTestService(settings)
    provider._test_key()  # Refuse to start this surface with a live/public/missing key.
    payments = SandboxPayments(database, provider)

    @router.post('/payments/paystack/test/webhook')
    async def webhook(request: Request):
        validate_json_request(request, max_bytes=65536)
        raw = await read_limited_body(request, max_bytes=65536)
        signature = request.headers.get('x-paystack-signature', '')
        try:
            provider.webhook_event(raw, signature)
        except PaystackError:
            raise HTTPException(403, 'Invalid test webhook') from None
        try:
            await payments.handle_event(raw, signature)
        except (PaystackError, SupabaseError):
            # Never acknowledge a failed verification/commit as fulfilled.
            raise HTTPException(503, 'Test payment awaiting reconciliation') from None
        return Response(status_code=200)

    return router

"""Test payment coordination. Only stored intents can become sandbox entitlements."""
import hashlib
import re

from bot.services.paystack_service import PaystackError, TestPaymentIntent, TestCheckout


class SandboxPayments:
    def __init__(self, database, provider):
        self.db = database
        self.provider = provider

    async def intent(self, reference, *, user_id=None):
        if not isinstance(reference, str) or not re.fullmatch(r'bw-test-[a-f0-9]{32}', reference):
            raise PaystackError('Invalid test reference')
        params = {'reference': f'eq.{reference}', 'select': '*', 'limit': '1'}
        if user_id is not None:
            params['user_id'] = f'eq.{user_id}'
        rows = await self.db._request('GET', '/rest/v1/paystack_test_intents', params=params)
        return rows[0] if rows else None

    async def checkout(self, user_id, request_key, email):
        intent = self.provider.new_intent()
        if intent.amount_kobo != 300000:
            raise PaystackError('Sandbox monthly price must be NGN 3000')
        rows = await self.db._request('POST', '/rest/v1/paystack_test_intents',
            params={'on_conflict': 'user_id,request_key'},
            headers={'Prefer': 'resolution=ignore-duplicates,return=representation'},
            json={'reference': intent.reference, 'user_id': user_id,
                  'request_key': request_key, 'amount_kobo': intent.amount_kobo})
        if not rows:
            rows = await self.db._request('GET', '/rest/v1/paystack_test_intents', params={
                'user_id': f'eq.{user_id}', 'request_key': f'eq.{request_key}', 'select': '*', 'limit': '1'})
            if rows and rows[0].get('checkout_url'):
                return TestCheckout(rows[0]['reference'], rows[0]['checkout_url'])
            raise PaystackError('Checkout already attempted; reconcile the saved test reference before retrying.')
        checkout = await self.provider.initialize_checkout(intent, email=email)
        await self.db._request('PATCH', '/rest/v1/paystack_test_intents',
            params={'reference': f'eq.{intent.reference}', 'user_id': f'eq.{user_id}'},
            json={'checkout_url': checkout.authorization_url})
        return checkout

    async def _receipt(self, reference, digest, kind):
        await self.db._request('POST', '/rest/v1/paystack_test_events',
            params={'on_conflict': 'digest'},
            headers={'Prefer': 'resolution=ignore-duplicates,return=minimal'},
            json={'digest': digest, 'reference': reference, 'event_type': kind})

    async def _apply(self, reference, digest, transaction_id, action):
        return await self.db._rpc('apply_test_payment', {'p_reference': reference,
            'p_digest': digest, 'p_transaction': transaction_id, 'p_action': action})

    async def reconcile(self, reference, user_id):
        intent = await self.intent(reference, user_id=user_id)
        if intent is None:
            raise PaystackError('Test payment was not found for this student')
        evidence = await self.provider.verify_payment(TestPaymentIntent(reference, intent['amount_kobo']))
        digest = hashlib.sha256(f'verify:{reference}:{evidence.transaction_id}'.encode()).hexdigest()
        await self._receipt(reference, digest, 'independent.verify')
        return await self._apply(reference, digest, evidence.transaction_id, 'paid')

    async def handle_event(self, raw, signature):
        event = self.provider.webhook_event(raw, signature)
        kind = event.get('event')
        if kind not in {'charge.success', 'refund.pending', 'refund.processing', 'refund.processed',
                        'refund.failed', 'charge.dispute.create', 'charge.dispute.remind', 'charge.dispute.resolve'}:
            return 'ignored'
        data = event.get('data')
        if not isinstance(data, dict):
            raise PaystackError('Invalid test event')
        digest = hashlib.sha256(raw).hexdigest()
        if kind == 'charge.success':
            if data.get('domain') != 'test':
                raise PaystackError('Expected a test event')
            reference = data.get('reference')
            intent = await self.intent(reference)
            if intent is None:
                return 'unknown'
            await self._receipt(reference, digest, kind)
            evidence = await self.provider.verify_payment(TestPaymentIntent(reference, intent['amount_kobo']))
            return await self._apply(reference, digest, evidence.transaction_id, 'paid')

        event_id = data.get('id')
        if type(event_id) is not int or event_id <= 0:
            raise PaystackError('Invalid adjustment identifier')
        resource = 'refund' if kind.startswith('refund.') else 'dispute'
        adjustment = await self.provider._request('GET', f'/{resource}/{event_id}')
        transaction = adjustment.get('transaction')
        transaction_id = transaction.get('id') if isinstance(transaction, dict) else transaction
        if (adjustment.get('id') != event_id or adjustment.get('domain') != 'test'
                or type(transaction_id) is not int or transaction_id <= 0):
            raise PaystackError('Invalid test adjustment')
        # Fetch the original transaction independently, even for refunded charges.
        original = await self.provider._request('GET', f'/transaction/{transaction_id}')
        reference = original.get('reference')
        intent = await self.intent(reference)
        if intent is None:
            return 'unknown'
        if (original.get('domain') != 'test' or original.get('id') != transaction_id
                or original.get('currency') != 'NGN' or original.get('amount') != intent['amount_kobo']):
            raise PaystackError('Adjustment did not match stored test payment')
        action = 'suspended'
        if resource == 'refund':
            amount = adjustment.get('amount')
            if (adjustment.get('currency') != 'NGN' or type(amount) is not int
                    or not 0 < amount <= intent['amount_kobo']):
                raise PaystackError('Invalid refund amount')
            if adjustment.get('status') == 'processed' and amount == intent['amount_kobo']:
                action = 'refunded'
        # Partial refunds, failed refunds and resolved disputes remain on hold for
        # explicit review. A stale event can never silently restore paid access.
        await self._receipt(reference, digest, kind)
        return await self._apply(reference, digest, transaction_id, action)

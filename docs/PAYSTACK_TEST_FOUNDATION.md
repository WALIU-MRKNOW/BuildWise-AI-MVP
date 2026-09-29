# Student Premium: Paystack sandbox foundation

Student Premium is planned at **₦3,000** and uses Paystack. Its duration, benefits,
paid limits and refund policy remain undecided. It is separate from BuildWise Pro.

## Implemented stage

`bot/services/paystack_service.py` provides an internal adapter using the existing
HTTP client dependency. It is deliberately not wired into Telegram or FastAPI:

- Disabled by default with `PAYSTACK_TEST_ENABLED=false`; the adapter requires an
  `sk_test_` secret. Live and public keys are rejected on every operation.
- Server-generated test references and immutable price snapshots use integer
  kobo: ₦3,000 becomes 300000. There is no subscription plan or billing period.
- Checkout initialization uses the fixed Paystack HTTPS API origin, bounded
  timeouts and no redirects or automatic retries. Returned checkout URLs must
  use `https://checkout.paystack.com` and match the original reference.
- Verification calls Paystack independently and requires test domain, successful
  status, exact reference, NGN currency, exact integer amount and transaction ID.
- Webhook helpers authenticate HMAC-SHA512 over the original bytes before parsing
  bounded JSON. A signed test charge yields only a reference for lookup.
- Provider errors are sanitized. Raw responses, card authorizations, email,
  signatures and keys are not logged or returned in verification evidence.

There is **no public payment route, checkout command, ledger, entitlement grant,
expiry, refund processing or recurring billing** in this stage. Test success
does not change student limits. Existing intro/onboarding and Pro gates are
unchanged. No migration or production secret is required to deploy this code.
`STUDENT_PREMIUM_ENABLED` stays false. Keep the sandbox flag false in production.

## Verification and sandbox use

Run `python -m unittest discover -s tests -v`. The Paystack tests use HTTP mock
transports and synthetic signatures; they never charge an account or call Paystack.
Passing them is not proof of a real sandbox checkout/webhook round trip.

For future internal sandbox tooling, set the sandbox flag and supply a test secret
through environment configuration. Do not paste keys into chats or commit them.
The adapter has four entry points: `new_intent`, `initialize_checkout`,
`verify_payment`, and `webhook_reference`. It performs no persistence itself.
An intent must be saved with the authenticated student's identity **before**
checkout initialization. On timeout or uncertain provider response, reconcile
that reference instead of blindly starting a replacement transaction.

## Next stage before exposing checkout

1. Inspect the current Supabase catalog and implement an additive private payment
   ledger with immutable student/reference/price/test-domain binding, unique
   provider transaction IDs and atomic state transitions. Do not use saved history
   as a payment ledger. Validate the migration in a disposable database first.
2. Add a separately gated, authenticated and allowlisted sandbox coordinator.
   Resolve webhook references to stored intents; never construct an expected
   payment from client metadata, callback parameters or webhook amounts.
3. Persist webhook receipt durably before returning success. Independently verify
   charge events, deduplicate retries/replays transactionally, and handle failures,
   refunds, disputes, out-of-order events and uncertain checkout initialization.
   The signature helper alone provides no replay prevention or refund handling.
4. Finalize duration, exact benefits/limits and refund policy. Add isolated test
   entitlements, expiry and atomic fulfillment tests. Test events must never grant
   production access. Keep existing Free/Pilot/Pro boundaries intact.
5. Verify a real Paystack test checkout and signed webhook round trip, restart
   recovery, duplicate delivery and failed/refunded payments in isolated staging.
   A live-payment implementation and activation require a later explicit decision.

Official references checked for this implementation:

- [Transaction initialize and verify API](https://paystack.com/docs/api/transaction/)
- [Webhook signatures and delivery](https://paystack.com/docs/payments/webhooks/)
- [Test payments](https://paystack.com/docs/payments/test-payments/)

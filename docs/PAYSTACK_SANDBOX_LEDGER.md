# Monthly Student Premium sandbox

Decision: **NGN 3,000 for one calendar month**. This implementation uses one-time
test checkout, not recurring debit. Production Premium benefits/limits and refund
policy are not finalized. Real payments and real Premium access remain disabled.

## Boundaries

- Only `sk_test_` secrets are accepted. Enabling sandbox with a missing, public or
  live key refuses startup. Keys stay in secure server configuration, never chat.
- `PAYSTACK_TEST_ENABLED=false` by default. The webhook route does not exist while
  disabled. `STUDENT_PREMIUM_ENABLED` remains false. Pro is untouched.
- Checkout commands require private Telegram chat, normal identity/onboarding,
  and explicit `PAYSTACK_TEST_ALLOWED_TELEGRAM_IDS`. No implicit founder/admin bypass.
- Sandbox entitlement tables/functions never participate in production limits or
  Pro authorization. Test payments cannot grant real paid benefits.

## Data flow

1. Persist server-generated reference, authenticated owner, integer NGN amount,
   monthly period and Telegram update idempotency key before calling Paystack.
2. Initialize once. Store only the validated checkout URL. Do not blindly retry
   an ambiguous initialization; `/premiumtestcheck` checks the existing reference.
3. Bound webhook bodies to 64 KiB and verify raw-body HMAC-SHA512 before parsing.
4. Use the event to find an existing intent. Independently verify the transaction
   against Paystack: test domain, reference, ID, currency, amount and success.
5. Persist a receipt digest without raw payload/personal information. Fulfill and
   mark the receipt processed in one database transaction. Unique transaction IDs,
   intent references, receipts, and per-user row locking protect replay/renewal.
6. Grant one calendar month in UTC, clamping month-end dates using PostgreSQL
   calendar arithmetic. Renewals start at the latest active grant expiry or now.
   Read-time start/expiry checks do not rely on a cleanup scheduler.

Failed provider verification or database commit returns HTTP 503 so Paystack can
retry. Invalid signatures return 403; unknown references grant nothing. A successful
return from the browser is never payment proof.

## Refunds and disputes

Refund/dispute IDs are independently fetched with the test key. The original
transaction is independently fetched and matched to its immutable intent. Verified
full processed refunds revoke the associated grant. Partial/pending/processing/failed
refunds and disputes suspend it for manual review. Resolved disputes also remain
suspended until an explicit adjudication policy is approved. Late charge events can
never undo a hold/refund. Refund-before-charge events prevent later activation.

This is conservative sandbox handling, not a finalized customer refund policy.
The adapter does not issue refunds, accept disputes, upload evidence or debit money.
Those actions are done in Paystack test mode by the operator during acceptance
testing. No live refund/dispute action is exposed.

## Secure sandbox setup and acceptance

1. Restore Railway hosting and deploy passing `main`.
2. Apply migration 012 to BuildWise only. Keep live Premium disabled.
3. Enter the Paystack test secret directly in Railway, set the explicit tester IDs,
   then enable `PAYSTACK_TEST_ENABLED` in a controlled test environment.
4. Set Paystack's test webhook URL to
   `https://<BuildWise host>/payments/paystack/test/webhook`.
5. Tester runs `/premiumtest <test-email>` and completes checkout using Paystack's
   official test details. Use `/premiumtestcheck` and `/premiumteststatus` to verify.
6. Re-send an event, test separate renewals, decline/abandon checkout, test expiry,
   initiate sandbox refunds/disputes and verify the stored hold/revocation. Confirm
   actual student daily limits and Pro access never change.
7. Compare signed provider delivery, independently fetched transaction, receipt,
   grant/expiry and analytics. Do not treat mocked tests as this acceptance run.

Unit/HTTP transport tests and real PostgreSQL transaction/concurrency tests are
automated. A real provider checkout/webhook round trip requires securely configured
test credentials, a running endpoint and a tester completing checkout. Until then,
provider sandbox acceptance remains unverified. OpenAI billing is a separate gate.

References checked 2026-10-07:
- https://paystack.com/docs/payments/webhooks/
- https://paystack.com/docs/payments/verify-payments/
- https://paystack.com/docs/api/refund/
- https://paystack.com/docs/api/dispute/

Rollback: disable `PAYSTACK_TEST_ENABLED`; retain private ledgers for reconciliation.
No destructive migration or public paid activation is needed.

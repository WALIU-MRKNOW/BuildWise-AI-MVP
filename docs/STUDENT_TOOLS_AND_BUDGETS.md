# Student tools and spend controls

Apply migrations 010 and 011 to the existing BuildWise database before enabling
`STUDENT_TOOLS_ENABLED`. Both are additive and keep anonymous/authenticated roles
out of the private tables and RPCs. Keep Pro and payment flags unchanged.

## Student experience

- `/review`: up to 200 answers from the last 90 days, weakest-topic ranking,
  saved mistakes and targeted new questions. Answer writes and quiz completion
  are idempotent. Existing 3/5/10 question and difficulty choices remain.
- `/profile Programme | Level`: optional information outside onboarding.
  `/profile clear` removes it. No personalization claim is made yet.
- `/topics`: original Building Technology topic taxonomy and three original
  foundation checks. This is not an official OAU/other university syllabus.
- Attach a PDF with `/pdfstudy summary 1-4`, `terms`, `practice`, or `focus`.
  Only the selected pages reach the AI. Limits: 10 MB, 200 document pages,
  8 selected pages; existing output/retry bounds and daily request allowance apply.
  Scanned pages are sent visually; readability and answer accuracy need funded
  provider evaluation. The practice task produces three questions and an answer
  key in an answer, rather than an interactive quiz.
- `/siwes <notes>` and `/studio <notes>` organize supplied notes as a labelled
  draft. System instructions prohibit invented activity, attendance, dimensions,
  signatures, approvals or certification. This is a safeguard, not a guarantee
  of model factuality; students must check drafts against their actual records.
- Helpful / Not Helpful / Report Issue store only a rating, opaque answer ID and
  owner. Tokens expire after restart or ten newer answers. Details go through
  existing private `/support`; feedback does not copy private work to analytics.
- `/productstats`: explicit admin Telegram allowlist only, aggregate 30-day
  counts. Notification recipients alone do not get access. No names, uploaded
  files, answer text, free-text topics or individual histories are returned.

History-off omits new mistake question/answer/explanation text. `/history` deletion
also clears adaptive review records. A daily cleanup removes review entries after
90 days. Optional profile information is removed separately with `/profile clear`.
Internal completion deduplication markers contain only owner/session IDs and dates.

## Rights and provenance

Topic labels, objectives and foundation checks were newly authored for BuildWise.
No textbooks, examinations, institutional syllabi or proprietary standards were
copied or ingested. External pages are reference links only (checked 2026-10-06):

- NIST SI guide: https://www.nist.gov/pml/special-publication-811
  (the page warns that its SI definitions predate the 2019 revision; use its
  linked SP 330 for current definitions).
- NIST construction research: https://www.nist.gov/buildings-and-construction
- OSHA construction reading: https://www.osha.gov/publications/bytopic/construction

These are general reading aids, not Nigerian jurisdictional code approval or
institutional course equivalence. University adapters require verified current
course information and remain deferred.

## AI budget activation

`AI_BUDGET_ENABLED=false` preserves existing request limits by default. This means
the new dollar ledger/caps are NOT operational until explicitly configured.
Set daily, monthly and per-user daily caps in USD micros (1 USD = 1,000,000 micros).
Zero budgets deny all metered calls. UTC day/month windows apply.

`AI_MODEL_PRICES_JSON` maps exact model IDs to operator-verified
`input_usd_per_million`, `output_usd_per_million`, and `max_input_tokens` (the model's
full supported input ceiling). Image models require a conservative `image_max_usd`
covering the configured input, size and quality. Missing/invalid prices block that
model. No undocumented prices or conversion from NGN are assumed.

Every physical response/image call reserves its worst-case configured cost before
network activity, including application retries. SDK automatic retries are disabled
when metering is enabled. Settlement uses provider token totals without cached-token
discounts, so this is conservative estimated cost, not an invoice. Image and missing
usage responses retain their full reservation. Timeouts, cancellations and uncertain
errors also retain their reservation until operator reconciliation. Do not automatically
expire those holds. A settlement outage retains the hold while delivering the answer.

The same gateway covers student answers, quizzes, sketch structure/render calls and
Pro AI calls. Pro authentication and entitlements are unchanged. Telegram calls use
the authenticated internal user ID; non-Telegram calls share the null-subject cap.
The ledger contains model, feature, token counts, cost and status, never content.

Before activation: verify prices/input ceilings against the actual models, select
owner-approved caps, test concurrent reservations/UTC reset/retries, and compare
funded test usage against provider billing. Incorrect ceilings/prices cannot provide
a reliable spending bound. Existing daily request refunds remain separate from
provider cost: a failed student answer may still incur a provider charge.

## Rollback

Disable `STUDENT_TOOLS_ENABLED` and `AI_BUDGET_ENABLED` to restore the prior surfaces.
Leave additive tables in place; do not delete student data or payment/audit records.
Required onboarding, navigation, history, reminders and Pro separation are preserved.

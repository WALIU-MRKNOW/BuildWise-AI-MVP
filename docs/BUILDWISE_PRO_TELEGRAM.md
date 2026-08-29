# BuildWise Pro — Telegram Professional Report Beta

This follow-up integrates the Phase 1 Professional Report Generator from PR #9 into Telegram without changing the existing student commands or student AI/image/PDF flows.

## Product separation

- **BuildWise Student** remains the default Telegram experience.
- **BuildWise Pro** begins only through `/pro` and uses a namespaced `ConversationHandler`.
- `/pro_cancel` or the Pro Cancel buttons end only the professional workflow. Existing student `context.user_data` keys are not cleared.

## Access controls

```env
BUILDWISE_PRO_ENABLED=false
BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=
BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10
BUILDWISE_PRO_MAX_FILE_SIZE_MB=10
```

`BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS` is a comma-separated list of numeric Telegram user IDs. The allowlist is **deny-by-default**: if it is empty, nobody can enter `/pro`, even when `BUILDWISE_PRO_ENABLED=true`.

The browser preview is controlled independently:

```env
BUILDWISE_PRO_WEB_ENABLED=false
```

Keep this false for the initial Telegram production beta unless the browser preview is intentionally being deployed too.

## Telegram workflow

1. `/pro` — verify feature flag and beta allowlist.
2. Project/report information — free text, up to 2,000 characters.
3. Drawing/sketch — PDF, PNG, JPG/JPEG; extension and magic bytes are validated.
4. Site notes — typed factual observations, treated as user-provided evidence.
5. Optional company template — PDF, TXT, Markdown; text templates must be UTF-8 and are capped at 256 KB.
6. Optional standard/code name — contextual reference only, not authoritative standards retrieval.
7. Confirmation — user must press **Generate Draft Report** before an AI request is made.
8. Completion — concise Telegram summary plus a downloadable self-contained `.html` draft report.

The HTML format is intentional for the first beta: it needs no additional PDF/document-generation dependency and can preserve clear headings and mandatory safety text. PDF/DOCX export is deferred.

## Safety

Every generated document contains the server-enforced notice:

**DRAFT ENGINEERING ASSISTANCE ONLY**

The generated file always appends:

- Limitations / assumptions
- Uncertainties / missing information
- Professional review / sign-off

The underlying Pro service continues to reject fabricated dimensions, loads, site conditions, calculations, clauses, compliance claims, signatures, credentials, approvals, or certification. Drawings, templates, site notes, and uploaded text are treated as untrusted source material and cannot override the professional-safety instructions.

## Privacy and storage

- Professional drawings/templates are held only in Telegram workflow memory for the active request.
- They are not uploaded to BuildWise Supabase storage.
- Generated Pro reports are not saved to student history or saved items.
- OpenAI uses `store=False` through the existing `ProfessionalReportService`.
- Logs contain only safe metadata such as Telegram user ID, file type/size, template presence, standard-reference presence, usage count, outcome, and latency. They do not log report contents, site notes, template contents, or document bytes.

## Rate limiting

Telegram Pro uses the existing `BUILDWISE_PRO_DAILY_REQUEST_LIMIT` with a separate per-user process-local limiter. Failed model generation refunds the reservation. This is appropriate for the restricted one-replica beta but must be replaced with durable accounting before broad/public Pro rollout.

## Database

No database migration is required for this Telegram beta. No production Supabase schema or credentials need to change.

## Rollout order

1. Merge/deploy code with `BUILDWISE_PRO_ENABLED=false`.
2. Verify `/health` and normal student Telegram behavior.
3. Set `BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS` to the approved engineer/test accounts.
4. Set `BUILDWISE_PRO_WEB_ENABLED=false`.
5. Enable `BUILDWISE_PRO_ENABLED=true`.
6. Verify an unauthorized account is denied and an allowlisted account can complete `/pro`.
7. Inspect runtime logs for errors and confirm no uploaded/report content appears.

## Rollback

Fastest feature rollback: set `BUILDWISE_PRO_ENABLED=false`. This immediately prevents new `/pro` workflows without affecting student features.

Code rollback: redeploy the previous known-good production commit if a regression is detected. No database rollback is required because this feature adds no migration or persistent Pro data.

## Deferred work

Not included here: persistent templates, organizations, report history, DOCX/PDF export infrastructure, licensed standards retrieval/citations, Excel calculators, or drawing generation.

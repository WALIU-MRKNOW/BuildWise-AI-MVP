# BuildWise Pro — Professional Report Generator MVP

BuildWise Pro is a separate, feature-flagged professional workflow that sits alongside the student-focused BuildWise experience. It does not replace or alter the Telegram study, plan/sketch, building-image, PDF, quiz, goals, reminders, progress, referral, support, or admin workflows.

## MVP scope

The first Pro capability is a **Professional Report Generator** available at `/pro` only when `BUILDWISE_PRO_ENABLED=true`.

Inputs:

- one drawing/sketch: PDF, PNG, JPG/JPEG
- typed site notes
- optional project/document information
- optional company report template: PDF
- optional engineering code/standard reference supplied as text

Output is a structured **draft** report containing:

- project/document information
- summary
- observations
- drawing/sketch findings
- site-note findings
- relevant technical considerations
- referenced code/standard
- recommendations
- limitations/assumptions
- uncertainties/missing information
- review/sign-off section
- mandatory professional-review notice

If a PDF company template is supplied, the AI is instructed to follow its visible section structure as closely as practical while retaining mandatory safety/limitations sections.

## Architecture

```text
Student Telegram UI ──────────────┐
                                  ├── BuildWise backend / OpenAI configuration
BuildWise Pro /pro web surface ───┘
```

The Pro AI adapter subclasses the existing `OpenAIService`, so it uses the same OpenAI client configuration, model, timeout behavior, error translation, and JSON parsing utilities without changing existing student AI methods.

The MVP stores **no Pro uploads or generated reports in Supabase**. Files are read into memory, sent to the configured OpenAI Responses API with `store=False`, and discarded when the request completes. This avoids mixing professional documents with student records and minimizes privacy risk while the workflow is being evaluated.

## Professional safety boundary

Every report is draft engineering assistance. It must not be treated as signed, certified, approved, or final professional work.

BuildWise Pro is explicitly instructed not to invent:

- dimensions
- loads
- drawing details
- site conditions
- observations
- calculations
- code clauses
- standard requirements
- compliance conclusions
- signatures or professional credentials

Missing, unclear, or uncertain information must be surfaced in `uncertainties_missing_information`.

A user-entered code/standard name is treated only as a reference identifier. The MVP does not retrieve standards text and must not fabricate clauses or assert compliance from a name alone.

Calculations, code compliance, structural conclusions, design decisions, and drawings must be reviewed and approved by a suitably qualified professional before real-project use.

## Security and privacy

- feature flag defaults to `false`
- preview-only shared access key; not intended as final public authentication
- access key is exchanged server-side for a signed session cookie
- cookie is `HttpOnly`, `Secure`, `SameSite=Strict`
- report and logout POSTs require a session-bound CSRF token
- access/session secrets never appear in browser HTML or JavaScript
- the page contains no application JavaScript
- file extension **and magic signature** are validated
- file size is bounded before AI processing
- company templates are PDF-only in this MVP
- generated HTML escapes model output before rendering
- CSP, no-store, no-referrer, nosniff, and anti-framing headers are applied
- request logs contain file type/template presence and usage counts, not document contents
- AI failures refund the exact in-memory preview reservation

The rate limiter is process-local and resets when the preview process restarts. Replace it with durable organization/user usage accounting before a public Pro release.

## Environment variables

Required when enabled:

```env
BUILDWISE_PRO_ENABLED=true
BUILDWISE_PRO_ACCESS_KEY=<unique random secret, minimum 24 characters>
BUILDWISE_PRO_SESSION_SECRET=<unique random secret, minimum 32 characters>
BUILDWISE_PRO_SESSION_TTL_SECONDS=28800
BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10
BUILDWISE_PRO_MAX_FILE_SIZE_MB=10
```

Do not reuse the Telegram token, OpenAI key, Supabase key, or admin password for either Pro secret.

Existing BuildWise `OPENAI_*` configuration is reused. No new database or storage environment variables are required for this MVP.

## Local testing

1. Create a local `.env` from `.env.example` using non-production credentials.
2. Set `BUILDWISE_PRO_ENABLED=true` and unique Pro preview secrets.
3. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Run the normal BuildWise API/runtime for your local environment.
5. Open `https://<your-local-or-preview-origin>/pro`. Secure cookies require HTTPS for browser testing; automated tests can validate cookie attributes without a public deployment.
6. Sign in with `BUILDWISE_PRO_ACCESS_KEY`.
7. Upload a small PDF/image drawing, enter factual site notes, optionally attach a PDF company template and standard name, then generate the draft.
8. Confirm the output contains the mandatory review notice and explicitly flags missing/uncertain information.

Run all tests with:

```bash
python -m unittest discover -s tests -v
```

## Preview deployment

Use a separate preview Railway service/environment. Keep production on `main`. Enable the Pro variables only on the preview service. Because this MVP does not use Pro-specific database tables or persistence, no Pro migration is required.

Do not enable `BUILDWISE_PRO_ENABLED` in production until review, preview testing, and professional feedback are complete.

## Future roadmap

### Phase 1 — current MVP
Professional Report Generator.

### Phase 2
Company Template Support beyond PDF and stronger template-field mapping/versioning.

### Phase 3
Engineering Code / Standards-Aware Workflows using licensed/authorized source material, retrieval, citations, jurisdiction/version controls, and explicit compliance review.

### Phase 4
Excel Design Calculator Generation with deterministic formulas, unit handling, validation, protected cells, verification tests, and professional approval workflow.

### Phase 5
Advanced Professional Deliverables / Drawing Assistance with strict confirmation, provenance, revision control, and professional sign-off requirements.

Excel calculator generation and drawing generation are intentionally **not implemented** in this MVP.

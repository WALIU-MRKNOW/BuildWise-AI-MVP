# BuildWise Student upgrade plan

This plan extends the existing Telegram Student MVP without replacing the current
intro, onboarding, navigation, history, progress, reminders, support, or working
analysis flows.

## Non-regression rules

- Preserve the current first-time name -> institution -> optional timezone flow.
- Keep the current role-aware home menu and existing Student/Pro/Admin boundaries.
- BuildWise Pro remains separate from Student Premium.
- Do not expose payment or Premium entitlement until metering and payment tests exist.
- Existing privacy, upload, webhook, admin and Telegram-ID authorization controls stay intact.

## Student Premium decision

- Planned Student Premium price point: **₦3,000**.
- STUDENT_PREMIUM_ENABLED remains false until entitlement/payment infrastructure is ready.
- Payment provider: **Paystack**, starting in test mode only.
- Billing period, refund rules and exact paid limits must be finalized
  before a public paid launch.

## Upgrade order

1. Reliability and cost controls
   - distinguish billing exhaustion from temporary provider rate limits;
   - add spend/usage monitoring and maintenance-safe messaging;
   - preserve refunds for failed AI requests.

2. Persistent and adaptive Practice
   - keep Easy/Medium/Hard and 3/5/10 question setup;
   - persist active sessions across bot restarts;
   - track weak topics and mistakes;
   - offer targeted retry/revision flows.

3. Optional richer student profile
   - programme/department, level and institution-specific preferences;
   - do not lengthen the required first-time onboarding.

4. Building Technology curriculum layer
   - rights-cleared topic/question bank;
   - worked measurement, units, materials, construction sequence and drawing-reading tasks;
   - university/course adapters only from current verified course information.

5. Visual Study & Solve
   - phone-friendly learning diagrams for theory and calculations;
   - diagrams must agree with stated values and assumptions.

6. Bounded PDF and drawing study tools
   - page-range focus, section summaries, practice generation and key-term extraction;
   - explicit size/cost limits for large files.

7. Sketch Studio progression
   - preserve existing sketch analysis and multi-view work;
   - progressively add conceptual elevation, 3D and annotation only when tested;
   - separate observed geometry from assumptions.

8. SIWES and studio support
   - organize factual notes into reflections, draft summaries, checklists and report structure;
   - never invent site activities or professional approvals.

9. In-product feedback
   - Helpful / Not Helpful / Report issue;
   - optional feedback reason;
   - aggregate product metrics without exposing private student content.

10. Free/Pilot/Premium entitlements
    - separate limits by tier;
    - keep Student Premium independent of BuildWise Pro;
    - meter costly PDF/image/visualization features separately.

11. Sustainability and continuity
    - documented school-representative handover;
    - durable founder/admin ownership and backups;
    - maintenance and incident process.

12. Admin product analytics
    - active students, institutions, Practice completion, studied topics, failures,
      feedback rates and feature usage/cost signals;
    - never expose student answer/file contents in aggregate dashboards.

13. Production hardening
    - smoke tests after deploys, rollback instructions, billing alerts,
      provider-error classification and maintenance mode.

14. Privacy and safety
    - private uploads, no secret/file-body logging, bounded retention;
    - learning assistance never claims structural certification, permit approval or sign-off.

## Current implementation status

- Student Premium price configuration exists at ₦3,000 but paid access is disabled.
- The [Paystack sandbox foundation](PAYSTACK_TEST_FOUNDATION.md) adds internal
  checkout/verification and signature helpers, disabled by default. Public payment
  routes, durable payment records and entitlement activation are a later stage.
- BuildWise Pro stays disabled unless separately enabled and allowlisted.
- Quick Practice generation/repeated-tap reliability fixes are already merged.
- Current production runtime remains Telegram webhook on Railway.

# Deploy Checklist: BuildWise AI MVP

**Date:** 2026-08-14  
**Deployer:** BuildWise owner

## Pre-deploy

- [ ] Revoke any Telegram token that was ever pasted into chat; use a new BotFather token.
- [ ] Create separate test and production Supabase projects where practical.
- [ ] Run `migrations/001_buildwise_mvp.sql` successfully.
- [ ] Confirm all tables, functions, indexes, RLS settings, and the private storage bucket exist.
- [ ] Confirm the anon key cannot read student tables.
- [ ] Add Telegram, OpenAI, and Supabase secrets only in Railway Variables.
- [ ] Set `ALLOWED_USER_IDS` during private testing.
- [ ] Run `python -m unittest discover -s tests -v` successfully.
- [ ] Confirm no secret or `.env` file is committed.
- [ ] Document who can access the revocable Supabase server secret key.

## Deploy

- [ ] Deploy one Railway replica using `railway.toml`.
- [ ] Confirm `/health` returns `status: ok`.
- [ ] Confirm logs show startup but never secret values or message contents.
- [ ] Send `/start` as a new Telegram user and set a timezone.
- [ ] Solve one text question and one image question.
- [ ] Test an ambiguous image and all four routing choices.
- [ ] Complete a 3-question Quick Practice and verify its score.
- [ ] Confirm streak and daily-goal values changed once for the local day.
- [ ] Test a building photo and verify the structural-safety disclaimer.
- [ ] Test a sketch and verify the approval disclaimer.
- [ ] Save an answer, view History, then test deletion with disposable data.
- [ ] Enable a near-future reminder time and confirm one reminder arrives.
- [ ] Restart Railway and confirm history, settings, streaks, and reminders persist.
- [ ] Send a duplicate test update only in a controlled integration test and confirm it is ignored.
- [ ] Exceed a low temporary daily limit and confirm further AI calls are blocked cleanly.

## Post-deploy

- [ ] Remove `ALLOWED_USER_IDS` only when public testing is approved.
- [ ] Monitor Railway restarts, Telegram errors, OpenAI errors, Supabase errors, and response latency.
- [ ] Review Supabase Storage growth and orphan uploads weekly during the MVP.
- [ ] Check that reminder volume remains at one automatic reminder per enabled user/day.
- [ ] Record user feedback on Study, Practice, Building, and Sketch usage separately.
- [ ] Keep only one polling replica active.

## Rollback triggers

- `/health` fails continuously after deployment.
- Telegram cannot complete `/start` or text solving.
- Streaks or goals increment incorrectly for multiple users.
- History is visible to the wrong Telegram account.
- The anon key can access protected student data.
- Duplicate or excessive reminders are sent.
- AI errors exceed 10% for 15 minutes, excluding deliberate rate-limit tests.

Rollback by redeploying the previous Railway revision. Do not reverse the database migration destructively during an incident; disable the service first, preserve data, and investigate with read-only queries.

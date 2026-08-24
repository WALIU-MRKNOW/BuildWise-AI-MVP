# ADR-001: BuildWise MVP Runtime and Persistence

**Status:** Accepted  
**Date:** 2026-08-14  
**Deciders:** BuildWise product owner

## Context

BuildWise must launch quickly with a low operational cost, support Telegram text/files and daily reminders, survive host restarts, and remain portable. The approved product stack is Telegram, Python/FastAPI, OpenAI, Supabase, and Railway. The first release should avoid AWS, multiple services, and a complex billing system.

## Decision

Run one Python asyncio process on Railway. It runs Telegram long polling and a small FastAPI health server together. OpenAI calls remain server-side. Supabase PostgreSQL is the source of truth for profiles, activity, streaks, goals, reminders, history, quizzes, and usage. A private Supabase Storage bucket contains uploads; database rows contain only object references.

Use atomic PostgreSQL functions for streak updates and daily usage. Use one periodic Telegram JobQueue task that queries due reminders from Supabase. Route obvious requests with deterministic keywords; ask the user when an unlabelled image is ambiguous rather than paying for a separate classifier call.

## Options Considered

### Option A: One Railway service + Supabase (selected)

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | Low for MVP traffic |
| Scalability | Adequate for early testing; one polling replica |
| Portability | High; standard Python, HTTP, PostgreSQL, and Docker |

**Pros:** one deploy, durable state, simple operations, no webhook requirement, private managed storage.  
**Cons:** polling is single-replica; reminder dispatch and bot traffic share one process.

### Option B: Separate bot, API, and worker services

| Dimension | Assessment |
|---|---|
| Complexity | Medium to high |
| Cost | Higher |
| Scalability | Better independent scaling |
| Portability | Medium |

**Pros:** fault and workload isolation.  
**Cons:** unnecessary queues, coordination, deployments, and cost before product fit.

### Option C: Local SQLite on Railway

| Dimension | Assessment |
|---|---|
| Complexity | Low initially |
| Cost | Low |
| Scalability | Poor |
| Portability | Medium |

**Pros:** minimal code.  
**Cons:** ephemeral-host data risk, awkward file persistence, and poor fit for reminders or future multi-instance work.

## Trade-off Analysis

Option A gives the fastest durable release and keeps every approved technology in a clear role. Single-replica Telegram polling is intentional during MVP testing. If real usage later requires horizontal scaling, Telegram can move to webhooks and reminders to a dedicated worker without replacing the domain or Supabase schema.

## Consequences

- Profiles, streaks, goals, history, usage, reminders, and files survive Railway restarts.
- The revocable Supabase secret key becomes a critical server-only secret.
- Only one polling replica should run for a Telegram token.
- Supabase availability is required for user flows.
- A future webhook/worker split remains possible without changing the public Telegram experience.

## Action Items

1. [x] Add the Supabase schema, indexes, RLS, private bucket, and atomic functions.
2. [x] Run Telegram and FastAPI in one portable asyncio runtime.
3. [x] Add durable reminders and duplicate-update protection.
4. [ ] Apply the migration to the owner’s Supabase project.
5. [ ] Add Railway secrets and run the live smoke checklist.
6. [ ] Revisit webhooks and a separate worker only after usage proves they are needed.

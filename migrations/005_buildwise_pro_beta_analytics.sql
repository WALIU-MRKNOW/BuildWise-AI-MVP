-- BuildWise Pro Beta 1.1 metadata-only analytics.
-- Intentionally excludes drawings, site notes, report bodies, templates, project documents, and secrets.

create extension if not exists pgcrypto;

create table if not exists public.pro_beta_sessions (
    id uuid primary key default gen_random_uuid(),
    telegram_id bigint not null check (telegram_id > 0),
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    generation_status text not null default 'started'
        check (generation_status in ('started', 'success', 'failed')),
    generation_latency_ms integer check (generation_latency_ms is null or generation_latency_ms >= 0),
    pdf_success boolean,
    docx_success boolean,
    input_tokens bigint check (input_tokens is null or input_tokens >= 0),
    output_tokens bigint check (output_tokens is null or output_tokens >= 0),
    total_tokens bigint check (total_tokens is null or total_tokens >= 0),
    approximate_cost_usd numeric(12,6),
    created_at timestamptz not null default now()
);

create index if not exists pro_beta_sessions_telegram_id_idx
    on public.pro_beta_sessions (telegram_id);
create index if not exists pro_beta_sessions_started_at_idx
    on public.pro_beta_sessions (started_at desc);

create table if not exists public.pro_beta_feedback (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references public.pro_beta_sessions(id) on delete cascade,
    telegram_id bigint not null check (telegram_id > 0),
    feedback_type text not null check (feedback_type in ('rating', 'comment')),
    rating text check (rating is null or rating in ('helpful', 'needs_improvement')),
    feedback_text text check (feedback_text is null or char_length(feedback_text) between 1 and 1000),
    created_at timestamptz not null default now(),
    check (
        (feedback_type = 'rating' and rating is not null and feedback_text is null)
        or
        (feedback_type = 'comment' and rating is null and feedback_text is not null)
    )
);

create index if not exists pro_beta_feedback_session_idx
    on public.pro_beta_feedback (session_id);
create index if not exists pro_beta_feedback_created_at_idx
    on public.pro_beta_feedback (created_at desc);

alter table public.pro_beta_sessions enable row level security;
alter table public.pro_beta_feedback enable row level security;

comment on table public.pro_beta_sessions is
    'BuildWise Pro beta operational metadata only; no engineering/project/report content.';
comment on table public.pro_beta_feedback is
    'BuildWise Pro beta feedback only; testers are warned not to include confidential project information.';

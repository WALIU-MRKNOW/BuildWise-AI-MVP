-- BuildWise Pro Beta 1.1: privacy-preserving operational analytics only.
-- Never store drawings, site notes, report bodies, templates, project documents, or secrets here.

create table if not exists public.pro_beta_reports (
    session_id uuid primary key,
    telegram_id bigint not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    generation_status text not null default 'started' check (generation_status in ('started','success','failed')),
    generation_latency_ms integer,
    pdf_success boolean,
    docx_success boolean,
    input_tokens bigint,
    output_tokens bigint,
    total_tokens bigint,
    estimated_cost_usd numeric(12,6),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists pro_beta_reports_telegram_id_idx
    on public.pro_beta_reports (telegram_id);
create index if not exists pro_beta_reports_started_at_idx
    on public.pro_beta_reports (started_at desc);

create table if not exists public.pro_beta_feedback (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references public.pro_beta_reports(session_id) on delete cascade,
    telegram_id bigint not null,
    rating text check (rating in ('helpful','needs_improvement')),
    feedback_text text check (feedback_text is null or char_length(feedback_text) between 1 and 1500),
    created_at timestamptz not null default now(),
    check (rating is not null or feedback_text is not null)
);

create index if not exists pro_beta_feedback_session_id_idx
    on public.pro_beta_feedback (session_id);
create index if not exists pro_beta_feedback_created_at_idx
    on public.pro_beta_feedback (created_at desc);

alter table public.pro_beta_reports enable row level security;
alter table public.pro_beta_feedback enable row level security;

-- Server-side service-role access bypasses RLS. No public policies are created.
comment on table public.pro_beta_reports is
    'BuildWise Pro beta operational metadata only. No engineering content or uploaded file data.';
comment on table public.pro_beta_feedback is
    'BuildWise Pro beta tester feedback linked only by safe session reference and Telegram ID.';

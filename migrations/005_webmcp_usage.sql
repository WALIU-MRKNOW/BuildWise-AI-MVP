-- BuildWise WebMCP POC usage accounting.
-- Web identities remain separate from Telegram users and all access is service-role only.

create table if not exists public.webmcp_usage_daily (
    subject_hash text not null,
    usage_date date not null,
    ai_requests integer not null default 0 check (ai_requests >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (subject_hash, usage_date)
);

create table if not exists public.webmcp_usage_reservations (
    id uuid primary key default gen_random_uuid(),
    subject_hash text not null,
    usage_date date not null,
    state text not null default 'reserved' check (state in ('reserved', 'finalized', 'refunded')),
    created_at timestamptz not null default now(),
    finalized_at timestamptz,
    refunded_at timestamptz
);

create index if not exists webmcp_usage_reservations_subject_date_idx
    on public.webmcp_usage_reservations(subject_hash, usage_date, created_at desc);

alter table public.webmcp_usage_daily enable row level security;
alter table public.webmcp_usage_reservations enable row level security;

revoke all on public.webmcp_usage_daily from public, anon, authenticated;
revoke all on public.webmcp_usage_reservations from public, anon, authenticated;
grant select, insert, update on public.webmcp_usage_daily to service_role;
grant select, insert, update on public.webmcp_usage_reservations to service_role;

create or replace function public.consume_webmcp_usage(
    p_subject_hash text,
    p_usage_date date,
    p_limit integer
)
returns table(
    allowed boolean,
    used integer,
    limit_value integer,
    reservation_id uuid
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_used integer;
    v_reservation_id uuid;
begin
    if p_subject_hash is null or length(btrim(p_subject_hash)) < 32 then
        raise exception 'invalid webmcp subject';
    end if;
    if p_limit is null or p_limit < 1 or p_limit > 1000 then
        raise exception 'invalid webmcp usage limit';
    end if;

    insert into public.webmcp_usage_daily(subject_hash, usage_date, ai_requests)
    values (p_subject_hash, p_usage_date, 0)
    on conflict (subject_hash, usage_date) do nothing;

    update public.webmcp_usage_daily as u
       set ai_requests = u.ai_requests + 1,
           updated_at = now()
     where u.subject_hash = p_subject_hash
       and u.usage_date = p_usage_date
       and u.ai_requests < p_limit
    returning u.ai_requests into v_used;

    if found then
        insert into public.webmcp_usage_reservations(subject_hash, usage_date)
        values (p_subject_hash, p_usage_date)
        returning id into v_reservation_id;
        return query select true, v_used, p_limit, v_reservation_id;
        return;
    end if;

    select u.ai_requests into v_used
      from public.webmcp_usage_daily as u
     where u.subject_hash = p_subject_hash
       and u.usage_date = p_usage_date;

    return query select false, coalesce(v_used, 0), p_limit, null::uuid;
end;
$$;

create or replace function public.finalize_webmcp_usage(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
    update public.webmcp_usage_reservations as r
       set state = 'finalized',
           finalized_at = now()
     where r.id = p_reservation_id
       and r.state = 'reserved';
    return found;
end;
$$;

create or replace function public.refund_webmcp_usage(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_subject_hash text;
    v_usage_date date;
begin
    update public.webmcp_usage_reservations as r
       set state = 'refunded',
           refunded_at = now()
     where r.id = p_reservation_id
       and r.state = 'reserved'
    returning r.subject_hash, r.usage_date into v_subject_hash, v_usage_date;

    if not found then
        return false;
    end if;

    update public.webmcp_usage_daily as u
       set ai_requests = greatest(0, u.ai_requests - 1),
           updated_at = now()
     where u.subject_hash = v_subject_hash
       and u.usage_date = v_usage_date;
    return true;
end;
$$;

revoke all on function public.consume_webmcp_usage(text, date, integer)
    from public, anon, authenticated;
revoke all on function public.finalize_webmcp_usage(uuid)
    from public, anon, authenticated;
revoke all on function public.refund_webmcp_usage(uuid)
    from public, anon, authenticated;

grant execute on function public.consume_webmcp_usage(text, date, integer) to service_role;
grant execute on function public.finalize_webmcp_usage(uuid) to service_role;
grant execute on function public.refund_webmcp_usage(uuid) to service_role;

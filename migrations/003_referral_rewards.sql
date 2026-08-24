-- BuildWise referral rewards v1.
-- One qualified referral grants the referrer 10 bonus AI requests.

create table if not exists public.referral_rewards (
    id uuid primary key default gen_random_uuid(),
    referrer_user_id uuid not null references public.users(id) on delete cascade,
    referred_user_id uuid not null references public.users(id) on delete cascade,
    credits_awarded integer not null check (credits_awarded > 0),
    qualified_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (referred_user_id),
    check (referrer_user_id <> referred_user_id)
);

create table if not exists public.referral_credit_balances (
    user_id uuid primary key references public.users(id) on delete cascade,
    available_credits integer not null default 0 check (available_credits >= 0),
    lifetime_earned integer not null default 0 check (lifetime_earned >= 0),
    lifetime_spent integer not null default 0 check (lifetime_spent >= 0),
    updated_at timestamptz not null default now(),
    check (lifetime_spent <= lifetime_earned)
);

create table if not exists public.usage_reservations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    usage_date date not null,
    source text not null check (source in ('daily', 'referral_credit')),
    finalized boolean not null default false,
    created_at timestamptz not null default now(),
    finalized_at timestamptz
);

create index if not exists referral_rewards_referrer_created_idx
    on public.referral_rewards(referrer_user_id, created_at desc);
create index if not exists usage_reservations_user_created_idx
    on public.usage_reservations(user_id, created_at desc);
create index if not exists usage_reservations_unfinalized_idx
    on public.usage_reservations(user_id, created_at)
    where finalized = false;

alter table public.referral_rewards enable row level security;
alter table public.referral_credit_balances enable row level security;
alter table public.usage_reservations enable row level security;

create or replace function public.consume_usage_with_referral_credit(
    p_user_id uuid,
    p_usage_date date,
    p_limit integer
)
returns table(
    allowed boolean,
    used integer,
    limit_value integer,
    reservation_id uuid,
    source text,
    bonus_credits_remaining integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_used integer;
    v_reservation uuid;
    v_source text;
    v_bonus integer := 0;
begin
    if p_limit < 1 then
        raise exception 'p_limit must be positive';
    end if;

    insert into public.usage_daily(user_id, usage_date, request_count)
    values (p_user_id, p_usage_date, 0)
    on conflict (user_id, usage_date) do nothing;

    update public.usage_daily
       set request_count = request_count + 1,
           updated_at = now()
     where user_id = p_user_id
       and usage_date = p_usage_date
       and request_count < p_limit
    returning request_count into v_used;

    if found then
        v_source := 'daily';
    else
        insert into public.referral_credit_balances(user_id)
        values (p_user_id)
        on conflict (user_id) do nothing;

        update public.referral_credit_balances
           set available_credits = available_credits - 1,
               lifetime_spent = lifetime_spent + 1,
               updated_at = now()
         where user_id = p_user_id
           and available_credits > 0
        returning available_credits into v_bonus;

        if not found then
            select request_count into v_used
              from public.usage_daily
             where user_id = p_user_id and usage_date = p_usage_date;
            select coalesce(available_credits, 0) into v_bonus
              from public.referral_credit_balances
             where user_id = p_user_id;
            return query select false, coalesce(v_used, 0), p_limit, null::uuid, null::text, coalesce(v_bonus, 0);
            return;
        end if;
        v_source := 'referral_credit';
        select request_count into v_used
          from public.usage_daily
         where user_id = p_user_id and usage_date = p_usage_date;
    end if;

    insert into public.usage_reservations(user_id, usage_date, source)
    values (p_user_id, p_usage_date, v_source)
    returning id into v_reservation;

    if v_source = 'daily' then
        select coalesce(available_credits, 0) into v_bonus
          from public.referral_credit_balances
         where user_id = p_user_id;
    end if;

    return query select true, coalesce(v_used, 0), p_limit, v_reservation, v_source, coalesce(v_bonus, 0);
end;
$$;

create or replace function public.finalize_usage_and_reward_referrer(
    p_reservation_id uuid,
    p_reward_credits integer default 10
)
returns table(
    reward_granted boolean,
    rewarded_user_id uuid,
    credits_awarded integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid;
    v_referrer_id uuid;
    v_inserted integer := 0;
begin
    if p_reward_credits < 1 or p_reward_credits > 1000 then
        raise exception 'invalid reward amount';
    end if;

    update public.usage_reservations
       set finalized = true,
           finalized_at = now()
     where id = p_reservation_id
       and finalized = false
    returning user_id into v_user_id;

    if not found then
        return query select false, null::uuid, 0;
        return;
    end if;

    select referred_by into v_referrer_id
      from public.users
     where id = v_user_id;

    if v_referrer_id is null or v_referrer_id = v_user_id then
        return query select false, null::uuid, 0;
        return;
    end if;

    insert into public.referral_rewards(
        referrer_user_id, referred_user_id, credits_awarded
    ) values (
        v_referrer_id, v_user_id, p_reward_credits
    )
    on conflict (referred_user_id) do nothing;
    get diagnostics v_inserted = row_count;

    if v_inserted = 1 then
        insert into public.referral_credit_balances(
            user_id, available_credits, lifetime_earned, lifetime_spent, updated_at
        ) values (
            v_referrer_id, p_reward_credits, p_reward_credits, 0, now()
        )
        on conflict (user_id) do update
           set available_credits = public.referral_credit_balances.available_credits + excluded.available_credits,
               lifetime_earned = public.referral_credit_balances.lifetime_earned + excluded.lifetime_earned,
               updated_at = now();
        return query select true, v_referrer_id, p_reward_credits;
        return;
    end if;

    return query select false, v_referrer_id, 0;
end;
$$;

create or replace function public.refund_usage_reservation(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_row public.usage_reservations%rowtype;
begin
    select * into v_row
      from public.usage_reservations
     where id = p_reservation_id
       and finalized = false
     for update;

    if not found then
        return false;
    end if;

    if v_row.source = 'daily' then
        update public.usage_daily
           set request_count = greatest(request_count - 1, 0),
               updated_at = now()
         where user_id = v_row.user_id
           and usage_date = v_row.usage_date;
    elsif v_row.source = 'referral_credit' then
        insert into public.referral_credit_balances(user_id, available_credits, lifetime_earned, lifetime_spent)
        values (v_row.user_id, 1, 1, 0)
        on conflict (user_id) do update
           set available_credits = public.referral_credit_balances.available_credits + 1,
               lifetime_spent = greatest(public.referral_credit_balances.lifetime_spent - 1, 0),
               updated_at = now();
    end if;

    update public.usage_reservations
       set finalized = true,
           finalized_at = now()
     where id = p_reservation_id;
    return true;
end;
$$;

revoke all on table public.referral_rewards from anon, authenticated;
revoke all on table public.referral_credit_balances from anon, authenticated;
revoke all on table public.usage_reservations from anon, authenticated;
revoke all on function public.consume_usage_with_referral_credit(uuid, date, integer) from public, anon, authenticated;
revoke all on function public.finalize_usage_and_reward_referrer(uuid, integer) from public, anon, authenticated;
revoke all on function public.refund_usage_reservation(uuid) from public, anon, authenticated;
grant execute on function public.consume_usage_with_referral_credit(uuid, date, integer) to service_role;
grant execute on function public.finalize_usage_and_reward_referrer(uuid, integer) to service_role;
grant execute on function public.refund_usage_reservation(uuid) to service_role;

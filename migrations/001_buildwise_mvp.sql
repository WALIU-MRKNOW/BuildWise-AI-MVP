-- BuildWise AI MVP schema for Supabase PostgreSQL.
-- Run once in Supabase Dashboard > SQL Editor before starting the bot.

create extension if not exists pgcrypto;

create table if not exists public.users (
    id uuid primary key default gen_random_uuid(),
    telegram_id bigint not null unique,
    username text,
    display_name text not null default 'Student',
    timezone text not null default 'UTC',
    timezone_confirmed boolean not null default false,
    timezone_prompted boolean not null default false,
    answer_style text not null default 'concise'
        check (answer_style in ('concise', 'detailed')),
    follow_up_buttons boolean not null default true,
    save_history boolean not null default true,
    default_daily_goal integer not null default 5
        check (default_daily_goal between 1 and 100),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.study_activity (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    activity_type text not null,
    subject text,
    topic text,
    units integer not null default 1 check (units > 0),
    completed boolean not null default true,
    activity_date date not null,
    created_at timestamptz not null default now()
);

create table if not exists public.streaks (
    user_id uuid primary key references public.users(id) on delete cascade,
    current_streak integer not null default 0,
    longest_streak integer not null default 0,
    last_activity_date date,
    total_study_days integer not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.daily_goals (
    user_id uuid not null references public.users(id) on delete cascade,
    target integer not null default 5 check (target between 1 and 100),
    completed_today integer not null default 0 check (completed_today >= 0),
    date date not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, date)
);

create table if not exists public.notification_preferences (
    user_id uuid primary key references public.users(id) on delete cascade,
    enabled boolean not null default false,
    reminder_time time not null default '19:00',
    timezone text not null default 'UTC',
    reminder_type text not null default 'streak'
        check (reminder_type in ('streak', 'daily_practice')),
    last_reminder_date date,
    snooze_until timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.conversations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    mode text not null check (mode in ('study', 'sketch', 'building')),
    message text not null,
    response text not null,
    category text,
    file_reference text,
    created_at timestamptz not null default now()
);

create table if not exists public.saved_items (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    item_type text not null,
    title text not null,
    content text not null,
    file_reference text,
    created_at timestamptz not null default now()
);

create table if not exists public.quiz_results (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    subject text,
    topic text not null,
    total_questions integer not null check (total_questions > 0),
    correct_answers integer not null check (correct_answers >= 0),
    score numeric(5,2) not null check (score between 0 and 100),
    created_at timestamptz not null default now(),
    check (correct_answers <= total_questions)
);

create table if not exists public.usage_daily (
    user_id uuid not null references public.users(id) on delete cascade,
    usage_date date not null,
    request_count integer not null default 0 check (request_count >= 0),
    updated_at timestamptz not null default now(),
    primary key (user_id, usage_date)
);

create table if not exists public.processed_updates (
    update_id bigint primary key,
    processed_at timestamptz not null default now()
);

create index if not exists study_activity_user_date_idx
    on public.study_activity(user_id, activity_date desc);
create index if not exists study_activity_user_topic_idx
    on public.study_activity(user_id, topic);
create index if not exists conversations_user_created_idx
    on public.conversations(user_id, created_at desc);
create index if not exists conversations_user_mode_created_idx
    on public.conversations(user_id, mode, created_at desc);
create index if not exists saved_items_user_created_idx
    on public.saved_items(user_id, created_at desc);
create index if not exists quiz_results_user_created_idx
    on public.quiz_results(user_id, created_at desc);
create index if not exists notification_enabled_idx
    on public.notification_preferences(enabled) where enabled = true;
create index if not exists processed_updates_time_idx
    on public.processed_updates(processed_at);

alter table public.users enable row level security;
alter table public.study_activity enable row level security;
alter table public.streaks enable row level security;
alter table public.daily_goals enable row level security;
alter table public.notification_preferences enable row level security;
alter table public.conversations enable row level security;
alter table public.saved_items enable row level security;
alter table public.quiz_results enable row level security;
alter table public.usage_daily enable row level security;
alter table public.processed_updates enable row level security;

-- No public policies are created. The trusted Railway backend uses the service
-- role key, which bypasses RLS; the anon key cannot read student data.

create or replace function public.upsert_telegram_user(
    p_telegram_id bigint,
    p_username text,
    p_display_name text
)
returns setof public.users
language plpgsql
security definer
set search_path = public
as $$
begin
    return query
    insert into public.users(telegram_id, username, display_name)
    values (p_telegram_id, p_username, coalesce(nullif(p_display_name, ''), 'Student'))
    on conflict (telegram_id) do update
       set username = excluded.username,
           display_name = excluded.display_name,
           updated_at = now()
    returning *;
end;
$$;

create or replace function public.claim_telegram_update(p_update_id bigint)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_inserted integer;
begin
    delete from public.processed_updates
     where processed_at < now() - interval '7 days';
    insert into public.processed_updates(update_id)
    values (p_update_id)
    on conflict (update_id) do nothing;
    get diagnostics v_inserted = row_count;
    return v_inserted = 1;
end;
$$;

create or replace function public.consume_daily_usage(
    p_user_id uuid,
    p_usage_date date,
    p_limit integer
)
returns table(allowed boolean, used integer, limit_value integer)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_used integer;
begin
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
        return query select true, v_used, p_limit;
    else
        select request_count into v_used
          from public.usage_daily
         where user_id = p_user_id and usage_date = p_usage_date;
        return query select false, coalesce(v_used, 0), p_limit;
    end if;
end;
$$;

create or replace function public.refund_daily_usage(
    p_user_id uuid,
    p_usage_date date
)
returns void
language sql
security definer
set search_path = public
as $$
    update public.usage_daily
       set request_count = greatest(request_count - 1, 0),
           updated_at = now()
     where user_id = p_user_id and usage_date = p_usage_date;
$$;

create or replace function public.record_study_completion(
    p_user_id uuid,
    p_activity_type text,
    p_subject text,
    p_topic text,
    p_local_date date,
    p_units integer default 1
)
returns table(
    current_streak integer,
    longest_streak integer,
    completed_today integer,
    daily_target integer,
    milestone integer,
    goal_just_completed boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_current integer := 0;
    v_longest integer := 0;
    v_total_days integer := 0;
    v_last_date date;
    v_target integer := 5;
    v_before integer := 0;
    v_after integer := 0;
    v_new_day boolean := false;
    v_milestone integer := null;
begin
    if p_units < 1 then
        raise exception 'p_units must be positive';
    end if;

    select default_daily_goal into v_target
      from public.users where id = p_user_id;

    insert into public.study_activity(
        user_id, activity_type, subject, topic, units, completed, activity_date
    ) values (
        p_user_id, p_activity_type, p_subject, p_topic, p_units, true, p_local_date
    );

    select s.current_streak, s.longest_streak, s.last_activity_date, s.total_study_days
      into v_current, v_longest, v_last_date, v_total_days
      from public.streaks s
     where s.user_id = p_user_id
     for update;

    if not found then
        v_current := 1;
        v_longest := 1;
        v_total_days := 1;
        v_new_day := true;
        insert into public.streaks(
            user_id, current_streak, longest_streak, last_activity_date,
            total_study_days, updated_at
        ) values (
            p_user_id, v_current, v_longest, p_local_date, v_total_days, now()
        );
    elsif v_last_date is distinct from p_local_date then
        v_new_day := true;
        if v_last_date = p_local_date - 1 then
            v_current := v_current + 1;
        else
            v_current := 1;
        end if;
        v_longest := greatest(v_longest, v_current);
        v_total_days := v_total_days + 1;
        update public.streaks
           set current_streak = v_current,
               longest_streak = v_longest,
               last_activity_date = p_local_date,
               total_study_days = v_total_days,
               updated_at = now()
         where user_id = p_user_id;
    end if;

    insert into public.daily_goals(user_id, target, completed_today, date)
    values (p_user_id, v_target, 0, p_local_date)
    on conflict (user_id, date) do nothing;

    select d.completed_today, d.target
      into v_before, v_target
      from public.daily_goals d
     where d.user_id = p_user_id and d.date = p_local_date
     for update;

    v_after := v_before + p_units;
    update public.daily_goals
       set completed_today = v_after, updated_at = now()
     where user_id = p_user_id and date = p_local_date;

    if v_new_day and v_current = any(array[3, 7, 14, 30, 50, 100]) then
        v_milestone := v_current;
    end if;

    return query
    select v_current, v_longest, v_after, v_target, v_milestone,
           (v_before < v_target and v_after >= v_target);
end;
$$;

create or replace function public.get_due_reminders(p_now timestamptz)
returns table(
    user_id uuid,
    telegram_id bigint,
    local_date date,
    current_streak integer,
    daily_target integer,
    daily_completed integer,
    reminder_type text
)
language sql
security definer
set search_path = public
as $$
    select
        n.user_id,
        u.telegram_id,
        local_values.local_day,
        case
            when s.last_activity_date is null then 0
            when s.last_activity_date < local_values.local_day - 1 then 0
            else coalesce(s.current_streak, 0)
        end,
        coalesce(d.target, u.default_daily_goal),
        coalesce(d.completed_today, 0),
        n.reminder_type
    from public.notification_preferences n
    join public.users u on u.id = n.user_id
    cross join lateral (
        select
            (p_now at time zone n.timezone)::date as local_day,
            (p_now at time zone n.timezone)::time as local_time
    ) local_values
    left join public.streaks s on s.user_id = n.user_id
    left join public.daily_goals d
      on d.user_id = n.user_id and d.date = local_values.local_day
    where n.enabled = true
      and n.last_reminder_date is distinct from local_values.local_day
      and (
          (
              n.snooze_until is not null
              and p_now >= n.snooze_until
              and p_now < n.snooze_until + interval '5 minutes'
          )
          or
          (
              n.snooze_until is null
              and local_values.local_time >= n.reminder_time
              and local_values.local_time < n.reminder_time + interval '5 minutes'
          )
      );
$$;

revoke all on function public.consume_daily_usage(uuid, date, integer) from public, anon, authenticated;
revoke all on function public.refund_daily_usage(uuid, date) from public, anon, authenticated;
revoke all on function public.record_study_completion(uuid, text, text, text, date, integer) from public, anon, authenticated;
revoke all on function public.get_due_reminders(timestamptz) from public, anon, authenticated;
revoke all on function public.upsert_telegram_user(bigint, text, text) from public, anon, authenticated;
revoke all on function public.claim_telegram_update(bigint) from public, anon, authenticated;
grant execute on function public.consume_daily_usage(uuid, date, integer) to service_role;
grant execute on function public.refund_daily_usage(uuid, date) to service_role;
grant execute on function public.record_study_completion(uuid, text, text, text, date, integer) to service_role;
grant execute on function public.get_due_reminders(timestamptz) to service_role;
grant execute on function public.upsert_telegram_user(bigint, text, text) to service_role;
grant execute on function public.claim_telegram_update(bigint) to service_role;

insert into storage.buckets (id, name, public, file_size_limit)
values ('buildwise-uploads', 'buildwise-uploads', false, 20971520)
on conflict (id) do update
set public = false, file_size_limit = excluded.file_size_limit;

-- Keep the duplicate-update table small without needing another scheduler.
delete from public.processed_updates
where processed_at < now() - interval '7 days';

-- BuildWise Pro usage must survive restarts and remain consistent across replicas.

create table if not exists public.pro_usage_daily (
  subject text not null,
  usage_date date not null,
  used integer not null default 0 check (used >= 0),
  updated_at timestamptz not null default now(),
  primary key (subject, usage_date)
);

alter table public.pro_usage_daily enable row level security;
revoke all on table public.pro_usage_daily from public, anon, authenticated;
grant select, insert, update, delete on table public.pro_usage_daily to service_role;

create or replace function public.consume_pro_beta_usage(
  p_subject text,
  p_usage_date date,
  p_limit integer
)
returns table(allowed boolean, used integer, limit_value integer)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_used integer;
begin
  if p_subject is null or length(p_subject) < 16 or length(p_subject) > 128 then
    raise exception 'invalid pro usage subject';
  end if;
  if p_limit < 1 or p_limit > 10000 then
    raise exception 'invalid pro usage limit';
  end if;

  insert into public.pro_usage_daily(subject, usage_date, used)
  values (p_subject, p_usage_date, 0)
  on conflict (subject, usage_date) do nothing;

  select d.used into v_used
  from public.pro_usage_daily d
  where d.subject = p_subject and d.usage_date = p_usage_date
  for update;

  if v_used >= p_limit then
    return query select false, v_used, p_limit;
    return;
  end if;

  update public.pro_usage_daily d
  set used = d.used + 1, updated_at = now()
  where d.subject = p_subject and d.usage_date = p_usage_date
  returning d.used into v_used;

  return query select true, v_used, p_limit;
end;
$$;

create or replace function public.refund_pro_beta_usage(
  p_subject text,
  p_usage_date date
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_used integer;
begin
  update public.pro_usage_daily d
  set used = greatest(0, d.used - 1), updated_at = now()
  where d.subject = p_subject and d.usage_date = p_usage_date
  returning d.used into v_used;
  return coalesce(v_used, 0);
end;
$$;

create or replace function public.get_pro_beta_usage(
  p_subject text,
  p_usage_date date
)
returns integer
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce((
    select d.used from public.pro_usage_daily d
    where d.subject = p_subject and d.usage_date = p_usage_date
  ), 0);
$$;

revoke all on function public.consume_pro_beta_usage(text,date,integer) from public, anon, authenticated;
revoke all on function public.refund_pro_beta_usage(text,date) from public, anon, authenticated;
revoke all on function public.get_pro_beta_usage(text,date) from public, anon, authenticated;
grant execute on function public.consume_pro_beta_usage(text,date,integer) to service_role;
grant execute on function public.refund_pro_beta_usage(text,date) to service_role;
grant execute on function public.get_pro_beta_usage(text,date) to service_role;

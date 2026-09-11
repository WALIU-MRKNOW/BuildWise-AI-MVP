-- Telegram BuildWise Pro uses the numeric Telegram ID as the server-side subject.
-- Keep the subject bounded while allowing normal Telegram ID lengths.

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
  if p_subject is null or length(p_subject) < 1 or length(p_subject) > 128 then
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

revoke all on function public.consume_pro_beta_usage(text,date,integer) from public, anon, authenticated;
grant execute on function public.consume_pro_beta_usage(text,date,integer) to service_role;

-- BuildWise student onboarding profile fields.
-- Existing users remain onboarded; only users created after this migration are prompted.

alter table public.users
    add column if not exists university text,
    add column if not exists onboarding_completed boolean not null default false;

-- Do not interrupt existing students with a new onboarding gate.
update public.users
   set onboarding_completed = true
 where onboarding_completed = false;

-- Preserve a student's chosen onboarding name after registration. Telegram
-- username/profile-name changes should not overwrite the name they entered.
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
           display_name = case
               when users.onboarding_completed then users.display_name
               else excluded.display_name
           end,
           updated_at = now()
    returning *;
end;
$$;

revoke all on function public.upsert_telegram_user(bigint, text, text)
    from public, anon, authenticated;
grant execute on function public.upsert_telegram_user(bigint, text, text)
    to service_role;

-- BuildWise referral tracking v1: attribution only, no rewards.

alter table public.users
    add column if not exists referral_code text;

alter table public.users
    add column if not exists referred_by uuid references public.users(id) on delete set null;

update public.users
   set referral_code = replace(id::text, '-', '')
 where referral_code is null or btrim(referral_code) = '';

alter table public.users
    alter column referral_code set not null;

create unique index if not exists users_referral_code_key
    on public.users(referral_code);

create index if not exists users_referred_by_idx
    on public.users(referred_by)
    where referred_by is not null;

alter table public.users enable row level security;

-- Keep generated links stable and collision-free by deriving the code from the
-- user's UUID. Existing users are backfilled above; future users get a code on insert.
create or replace function public.ensure_user_referral_code()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    if new.referral_code is null or btrim(new.referral_code) = '' then
        new.referral_code := replace(new.id::text, '-', '');
    else
        new.referral_code := lower(btrim(new.referral_code));
    end if;
    return new;
end;
$$;

drop trigger if exists users_referral_code_before_insert on public.users;
create trigger users_referral_code_before_insert
before insert on public.users
for each row execute function public.ensure_user_referral_code();

-- Atomically create/update a Telegram user and attribute a referral only when
-- that row is inserted for the first time. Existing users can never be attributed
-- later through this RPC, which prevents duplicate/late attribution races.
create or replace function public.register_referral_start(
    p_telegram_id bigint,
    p_username text,
    p_display_name text,
    p_referral_code text default null
)
returns table(
    user_id uuid,
    referral_code text,
    referred_by uuid,
    attributed boolean,
    was_new boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user public.users%rowtype;
    v_referrer_id uuid;
    v_was_new boolean := false;
    v_attributed boolean := false;
begin
    insert into public.users(telegram_id, username, display_name)
    values (
        p_telegram_id,
        p_username,
        coalesce(nullif(p_display_name, ''), 'Student')
    )
    on conflict (telegram_id) do nothing
    returning * into v_user;

    v_was_new := found;

    if not v_was_new then
        update public.users
           set username = p_username,
               display_name = coalesce(nullif(p_display_name, ''), 'Student'),
               updated_at = now()
         where telegram_id = p_telegram_id
        returning * into v_user;
    end if;

    if v_was_new and p_referral_code is not null and btrim(p_referral_code) <> '' then
        select u.id
          into v_referrer_id
          from public.users u
         where u.referral_code = lower(btrim(p_referral_code))
         limit 1;

        if v_referrer_id is not null and v_referrer_id <> v_user.id then
            update public.users
               set referred_by = v_referrer_id,
                   updated_at = now()
             where id = v_user.id
               and referred_by is null
            returning * into v_user;
            v_attributed := found;
        end if;
    end if;

    return query
    select v_user.id, v_user.referral_code, v_user.referred_by,
           v_attributed, v_was_new;
end;
$$;

-- Defense in depth: referral RPCs are backend-only just like existing BuildWise RPCs.
revoke all on function public.ensure_user_referral_code() from public, anon, authenticated;
revoke all on function public.register_referral_start(bigint, text, text, text) from public, anon, authenticated;
grant execute on function public.register_referral_start(bigint, text, text, text) to service_role;

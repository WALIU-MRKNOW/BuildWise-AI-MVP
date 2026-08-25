-- Fix referral attribution failure caused by PL/pgSQL output-column ambiguity.
-- Tracking/reward semantics are unchanged.

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
        update public.users as u
           set username = p_username,
               display_name = coalesce(nullif(p_display_name, ''), 'Student'),
               updated_at = now()
         where u.telegram_id = p_telegram_id
        returning u.* into v_user;
    end if;

    if v_was_new and p_referral_code is not null and btrim(p_referral_code) <> '' then
        select u.id
          into v_referrer_id
          from public.users as u
         where u.referral_code = lower(btrim(p_referral_code))
         limit 1;

        if v_referrer_id is not null and v_referrer_id <> v_user.id then
            update public.users as u
               set referred_by = v_referrer_id,
                   updated_at = now()
             where u.id = v_user.id
               and u.referred_by is null
            returning u.* into v_user;
            v_attributed := found;
        end if;
    end if;

    return query
    select v_user.id, v_user.referral_code, v_user.referred_by,
           v_attributed, v_was_new;
end;
$$;

revoke all on function public.register_referral_start(bigint, text, text, text)
    from public, anon, authenticated;
grant execute on function public.register_referral_start(bigint, text, text, text)
    to service_role;

-- Retire the BuildWise referral/reward system.
-- Historical migration files remain in the repository so fresh databases can
-- reproduce schema history before this cleanup migration removes the feature.

begin;

-- Functions must be removed before tables/columns they reference.
drop function if exists public.finalize_usage_and_reward_referrer(uuid, integer);
drop function if exists public.consume_usage_with_referral_credit(uuid, date, integer);
drop function if exists public.refund_usage_reservation(uuid);
drop function if exists public.register_referral_start(bigint, text, text, text);

drop trigger if exists users_referral_code_before_insert on public.users;
drop function if exists public.ensure_user_referral_code();

drop table if exists public.referral_rewards;
drop table if exists public.referral_credit_balances;
drop table if exists public.usage_reservations;

alter table public.users
    drop column if exists referred_by,
    drop column if exists referral_code;

commit;

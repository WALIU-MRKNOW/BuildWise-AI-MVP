-- Retire the old referral/reward system after the runtime no longer references it.
-- Historical referral data is intentionally deleted.

begin;

drop function if exists public.finalize_usage_and_reward_referrer(uuid, integer);
drop function if exists public.consume_usage_with_referral_credit(uuid, date, integer);
drop function if exists public.refund_usage_reservation(uuid);
drop function if exists public.register_referral_start(bigint, text, text, text);
drop function if exists public.ensure_user_referral_code();

drop table if exists public.usage_reservations cascade;
drop table if exists public.referral_credit_balances cascade;
drop table if exists public.referral_rewards cascade;

alter table public.users
  drop column if exists referred_by,
  drop column if exists referral_code;

commit;

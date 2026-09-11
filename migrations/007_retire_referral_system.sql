-- Retire the old referral/reward system after the runtime no longer references it.
-- Historical referral data is intentionally deleted.

begin;

-- Remove objects attached directly to users before dropping the helper function
-- or referral columns. Keeping this explicit makes the migration reproducible
-- without relying on broad CASCADE behavior against the core users table.
drop trigger if exists users_referral_code_before_insert on public.users;
alter table public.users drop constraint if exists users_referred_by_fkey;
drop index if exists public.users_referred_by_idx;
drop index if exists public.users_referral_code_key;

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

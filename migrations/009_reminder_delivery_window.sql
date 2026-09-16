-- Ensure enabled reminders are delivered on the next successful poll even when
-- Railway, Telegram, or the scheduler misses the original five-minute window.
-- last_reminder_date still guarantees at most one automatic reminder per local day.

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
          )
          or
          (
              n.snooze_until is null
              and local_values.local_time >= n.reminder_time
          )
      );
$$;

revoke all on function public.get_due_reminders(timestamptz) from public, anon, authenticated;
grant execute on function public.get_due_reminders(timestamptz) to service_role;

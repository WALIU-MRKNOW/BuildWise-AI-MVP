-- BuildWise student onboarding and privacy-conscious operational activity.
-- Existing users are grandfathered so the new onboarding flow does not interrupt them.

alter table public.users
    add column if not exists student_name text,
    add column if not exists university text,
    add column if not exists onboarding_completed boolean not null default false;

update public.users
   set student_name = coalesce(nullif(student_name, ''), nullif(display_name, ''), 'Student'),
       onboarding_completed = true
 where onboarding_completed = false;

alter table public.users
    drop constraint if exists users_student_name_length_check,
    add constraint users_student_name_length_check
        check (student_name is null or char_length(student_name) between 1 and 100),
    drop constraint if exists users_university_length_check,
    add constraint users_university_length_check
        check (university is null or char_length(university) between 2 and 160);

create table if not exists public.user_activity_events (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    event_type text not null,
    mode text,
    detail text,
    created_at timestamptz not null default now(),
    check (char_length(event_type) between 1 and 80),
    check (detail is null or char_length(detail) <= 500)
);

create index if not exists user_activity_events_created_idx
    on public.user_activity_events(created_at desc);
create index if not exists user_activity_events_user_created_idx
    on public.user_activity_events(user_id, created_at desc);

alter table public.user_activity_events enable row level security;

-- No public policies are created. The trusted Railway backend uses the service
-- role key and the browser never receives a Supabase secret.

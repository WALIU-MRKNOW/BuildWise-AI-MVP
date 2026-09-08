-- Persist the minimal student profile collected before BuildWise study access.
-- Existing users will be asked to complete onboarding on their next start/use.

alter table public.users
    add column if not exists student_name text,
    add column if not exists university text,
    add column if not exists onboarding_completed boolean not null default false,
    add column if not exists onboarding_completed_at timestamptz;

comment on column public.users.student_name is
    'Student-provided name used by BuildWise after onboarding.';
comment on column public.users.university is
    'Student-provided university, polytechnic, college, or institution.';
comment on column public.users.onboarding_completed is
    'True after the required name and institution onboarding steps are complete.';
comment on column public.users.onboarding_completed_at is
    'Timestamp when required student onboarding was completed.';

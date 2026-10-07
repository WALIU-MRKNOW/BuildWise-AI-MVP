-- Additive student tools. Private content never enters product analytics.
create table public.student_practice_answers (
    user_id uuid not null references public.users(id) on delete cascade,
    session_id text not null check (length(session_id) between 1 and 40),
    question_index integer not null check (question_index between 0 and 9),
    topic text not null check (length(topic) between 1 and 200),
    correct boolean not null,
    selected_index integer not null check (selected_index between 0 and 3),
    question text not null default '' check (length(question) <= 2000),
    answer text not null default '' check (length(answer) <= 1000),
    explanation text not null default '' check (length(explanation) <= 3000),
    created_at timestamptz not null default now(),
    primary key (user_id, session_id, question_index)
);
create index student_practice_recent on public.student_practice_answers(user_id, created_at desc);
create table public.student_optional_profiles (
    user_id uuid primary key references public.users(id) on delete cascade,
    programme text not null check (length(programme) between 1 and 100),
    level text not null check (length(level) between 1 and 40)
);
create table public.student_feedback (
    user_id uuid not null references public.users(id) on delete cascade,
    answer_id uuid not null,
    rating text not null check (rating in ('helpful','unhelpful','issue')),
    created_at timestamptz not null default now(),
    primary key (user_id, answer_id)
);
alter table public.student_practice_answers enable row level security;
alter table public.student_optional_profiles enable row level security;
alter table public.student_feedback enable row level security;
revoke all on public.student_practice_answers, public.student_optional_profiles, public.student_feedback from public, anon, authenticated;
grant all on public.student_practice_answers, public.student_optional_profiles, public.student_feedback to service_role;

create table public.student_practice_completions (
 user_id uuid not null references public.users(id) on delete cascade,
 session_id text not null, created_at timestamptz not null default now(),
 primary key(user_id,session_id)
);
alter table public.student_practice_completions enable row level security;
revoke all on public.student_practice_completions from public,anon,authenticated;
grant all on public.student_practice_completions to service_role;
create function public.finish_student_practice(p_user_id uuid,p_session_id text,p_topic text,
 p_total integer,p_correct integer,p_date date) returns jsonb
language plpgsql security definer set search_path=public as $$
declare result jsonb;
begin
 if p_total not between 1 and 10 or p_correct not between 0 and p_total
 or length(p_topic) not between 1 and 200 or length(p_session_id) not between 1 and 40
 then raise exception 'Invalid practice result'; end if;
 perform 1 from users where id=p_user_id for update;
 insert into student_practice_completions(user_id,session_id) values(p_user_id,p_session_id)
 on conflict do nothing;
 if not found then return jsonb_build_object('already_completed',true); end if;
 insert into quiz_results(user_id,topic,total_questions,correct_answers,score)
 values(p_user_id,p_topic,p_total,p_correct,round(100.0*p_correct/p_total,2));
 select to_jsonb(c) into result from record_study_completion(p_user_id,'quiz',null,p_topic,p_date,p_total) c;
 return result;
end; $$;
revoke all on function public.finish_student_practice(uuid,text,text,integer,integer,date) from public,anon,authenticated;
grant execute on function public.finish_student_practice(uuid,text,text,integer,integer,date) to service_role;

create function public.student_product_analytics() returns jsonb
language sql security definer set search_path = public as $$
 select jsonb_build_object(
   'active_students_30d', (select count(distinct user_id) from study_activity where created_at >= now() - interval '30 days'),
   'practice_answers_30d', (select count(*) from student_practice_answers where created_at >= now() - interval '30 days'),
   'practice_correct_30d', (select count(*) from student_practice_answers where correct and created_at >= now() - interval '30 days'),
   'practice_completions_30d', (select count(*) from quiz_results where created_at >= now() - interval '30 days'),
   'helpful_30d', (select count(*) from student_feedback where rating='helpful' and created_at >= now() - interval '30 days'),
   'unhelpful_30d', (select count(*) from student_feedback where rating='unhelpful' and created_at >= now() - interval '30 days'),
   'issues_30d', (select count(*) from student_feedback where rating='issue' and created_at >= now() - interval '30 days')
 );
$$;
revoke all on function public.student_product_analytics() from public, anon, authenticated;
grant execute on function public.student_product_analytics() to service_role;

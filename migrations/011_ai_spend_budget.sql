-- USD micros, UTC calendar windows. Reservations survive restarts and uncertainty.
create table public.ai_spend_ledger (
 id uuid primary key, user_id uuid references public.users(id) on delete set null,
 feature text not null check (feature in ('response','image')),
 model text not null check (length(model) between 1 and 100),
 reserved_micros bigint not null check (reserved_micros > 0),
 charged_micros bigint not null check (charged_micros >= 0),
 input_tokens bigint check (input_tokens >= 0), output_tokens bigint check (output_tokens >= 0),
 state text not null default 'reserved' check (state in ('reserved','settled','uncertain')),
 created_at timestamptz not null default now()
);
create index ai_spend_time on public.ai_spend_ledger(created_at);
create index ai_spend_user_time on public.ai_spend_ledger(user_id, created_at);
alter table public.ai_spend_ledger enable row level security;
revoke all on public.ai_spend_ledger from public, anon, authenticated;
grant all on public.ai_spend_ledger to service_role;

create function public.reserve_ai_spend(p_id uuid, p_user_id uuid, p_feature text, p_model text,
 p_reserve bigint, p_daily bigint, p_monthly bigint, p_user_daily bigint) returns boolean
language plpgsql security definer set search_path=public as $$
declare d timestamptz := date_trunc('day', now() at time zone 'UTC') at time zone 'UTC';
 m timestamptz := date_trunc('month', now() at time zone 'UTC') at time zone 'UTC';
begin
 if p_reserve <= 0 or p_daily <= 0 or p_monthly <= 0 or p_user_daily <= 0 then return false; end if;
 perform pg_advisory_xact_lock(72104811);
 -- Reusing a reservation must NEVER authorize another network call.
 if exists(select 1 from ai_spend_ledger where id=p_id) then return false; end if;
 if coalesce((select sum(charged_micros) from ai_spend_ledger where created_at>=d),0)+p_reserve > p_daily
 or coalesce((select sum(charged_micros) from ai_spend_ledger where created_at>=m),0)+p_reserve > p_monthly
 or coalesce((select sum(charged_micros) from ai_spend_ledger where created_at>=d and user_id is not distinct from p_user_id),0)+p_reserve > p_user_daily
 then return false; end if;
 insert into ai_spend_ledger(id,user_id,feature,model,reserved_micros,charged_micros)
 values(p_id,p_user_id,p_feature,p_model,p_reserve,p_reserve);
 return true;
end; $$;
create function public.settle_ai_spend(p_id uuid, p_charge bigint, p_input bigint, p_output bigint, p_uncertain boolean)
returns void language plpgsql security definer set search_path=public as $$
begin
 perform pg_advisory_xact_lock(72104811);
 update ai_spend_ledger set
 charged_micros=case when p_uncertain then greatest(reserved_micros,coalesce(p_charge,0)) else p_charge end,
 input_tokens=p_input, output_tokens=p_output,
 state=case when p_uncertain then 'uncertain' else 'settled' end
 where id=p_id and state='reserved';
end; $$;
create function public.ai_spend_totals() returns jsonb
language sql security definer set search_path=public as $$
 select jsonb_build_object('calls_30d',count(*),'charged_usd_micros_30d',coalesce(sum(charged_micros),0),
 'reserved_or_uncertain_30d',count(*) filter(where state <> 'settled'),
 'input_tokens_30d',coalesce(sum(input_tokens),0),'output_tokens_30d',coalesce(sum(output_tokens),0))
 from ai_spend_ledger where created_at>=now()-interval '30 days';
$$;
revoke all on function public.reserve_ai_spend(uuid,uuid,text,text,bigint,bigint,bigint,bigint),
 public.settle_ai_spend(uuid,bigint,bigint,bigint,boolean), public.ai_spend_totals() from public,anon,authenticated;
grant execute on function public.reserve_ai_spend(uuid,uuid,text,text,bigint,bigint,bigint,bigint),
 public.settle_ai_spend(uuid,bigint,bigint,bigint,boolean), public.ai_spend_totals() to service_role;

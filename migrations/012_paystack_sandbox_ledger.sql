-- Test-only ledger. These tables cannot confer production Student Premium/Pro.
create table public.paystack_test_intents (
 reference text primary key check(reference ~ '^bw-test-[a-f0-9]{32}$'),
 user_id uuid not null references public.users(id),
 request_key text not null check(length(request_key) between 1 and 100),
 amount_kobo integer not null check(amount_kobo=300000),
 currency text not null default 'NGN' check(currency='NGN'),
 billing_period text not null default 'monthly' check(billing_period='monthly'),
 domain text not null default 'test' check(domain='test'),
 transaction_id bigint unique check(transaction_id>0),
 status text not null default 'created' check(status in ('created','paid','suspended','refunded')),
 checkout_url text,
 created_at timestamptz not null default now(),
 unique(user_id,request_key)
);
create table public.paystack_test_events (
 digest text primary key check(digest ~ '^[a-f0-9]{64}$'),
 reference text not null references public.paystack_test_intents(reference),
 event_type text not null check(length(event_type)<=60),
 received_at timestamptz not null default now(), processed_at timestamptz
);
create table public.paystack_test_entitlements (
 reference text primary key references public.paystack_test_intents(reference),
 user_id uuid not null references public.users(id),
 starts_at timestamptz not null, expires_at timestamptz not null,
 state text not null default 'active' check(state in ('active','suspended','revoked')),
 check(expires_at>starts_at)
);
create index paystack_test_user_expiry on public.paystack_test_entitlements(user_id, expires_at);
alter table public.paystack_test_intents enable row level security;
alter table public.paystack_test_events enable row level security;
alter table public.paystack_test_entitlements enable row level security;
revoke all on public.paystack_test_intents,public.paystack_test_events,public.paystack_test_entitlements from public,anon,authenticated;
grant all on public.paystack_test_intents,public.paystack_test_events,public.paystack_test_entitlements to service_role;

create function public.apply_test_payment(p_reference text,p_digest text,p_transaction bigint,p_action text)
returns text language plpgsql security definer set search_path=public as $$
declare intent paystack_test_intents%rowtype; anchor timestamptz; uid uuid;
begin
 if p_action is null or p_action not in ('paid','suspended','refunded')
  or p_transaction is null or p_transaction<=0 then
  raise exception 'Invalid test payment action'; end if;
 select user_id into uid from paystack_test_intents where reference=p_reference;
 if uid is null then raise exception 'Unknown intent'; end if;
 -- Serialize renewals/refunds for this user, then lock the immutable intent.
 perform 1 from users where id=uid for update;
 select * into intent from paystack_test_intents where reference=p_reference for update;
 if not exists(select 1 from paystack_test_events where digest=p_digest and reference=p_reference) then
  raise exception 'Receipt required'; end if;
 if intent.transaction_id is not null and intent.transaction_id<>p_transaction then
  raise exception 'Transaction mismatch'; end if;
 if exists(select 1 from paystack_test_events where digest=p_digest and processed_at is not null) then
  return intent.status; end if;
 if p_action='paid' and intent.status='created' then
  select greatest(now(), coalesce(max(expires_at),now())) into anchor
   from paystack_test_entitlements where user_id=uid and state='active';
  insert into paystack_test_entitlements(reference,user_id,starts_at,expires_at)
   values(p_reference,uid,anchor,((anchor at time zone 'UTC') + interval '1 month') at time zone 'UTC');
  update paystack_test_intents set status='paid',transaction_id=p_transaction where reference=p_reference;
 elsif p_action in ('suspended','refunded') then
  -- A late charge.success must never reverse a refund/dispute hold.
  update paystack_test_intents set status=case when status='refunded' then status else p_action end,
   transaction_id=p_transaction where reference=p_reference;
  update paystack_test_entitlements set state=case when state='revoked' or p_action='refunded' then 'revoked' else 'suspended' end
   where reference=p_reference;
 end if;
 update paystack_test_events set processed_at=now() where digest=p_digest;
 return (select status from paystack_test_intents where reference=p_reference);
end; $$;
create function public.test_premium_active(p_user_id uuid) returns boolean
language sql security definer set search_path=public as $$
 select exists(select 1 from paystack_test_entitlements where user_id=p_user_id
  and state='active' and starts_at<=now() and expires_at>now());
$$;
revoke all on function public.apply_test_payment(text,text,bigint,text),public.test_premium_active(uuid) from public,anon,authenticated;
grant execute on function public.apply_test_payment(text,text,bigint,text),public.test_premium_active(uuid) to service_role;

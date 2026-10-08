begin;
set local timezone='UTC';
do $$
declare u uuid := gen_random_uuid(); r text := 'bw-test-'||replace(gen_random_uuid()::text,'-','');
 r2 text := 'bw-test-'||replace(gen_random_uuid()::text,'-','');
 r3 text := 'bw-test-'||replace(gen_random_uuid()::text,'-','');
 r4 text := 'bw-test-'||replace(gen_random_uuid()::text,'-','');
 e text := repeat('a',64); e2 text := repeat('b',64); e3 text := repeat('c',64);
 original_expiry timestamptz; result text;
begin
 insert into users(id,telegram_id,display_name) values(u,-1000000002,'rollback-payments');
 insert into paystack_test_intents(reference,user_id,request_key,amount_kobo)
 values(r,u,'one',300000),(r2,u,'two',300000),(r3,u,'three',300000);
 insert into paystack_test_events(digest,reference,event_type) values(e,r,'charge.success');
 result := apply_test_payment(r,e,111,'paid');
 if result<>'paid' or not test_premium_active(u) then raise exception 'Activation failed'; end if;
 select expires_at into original_expiry from paystack_test_entitlements where reference=r;
 if original_expiry <> now()+interval '1 month' then raise exception 'Wrong monthly expiry'; end if;
 perform apply_test_payment(r,e,111,'paid');
 insert into paystack_test_events(digest,reference,event_type) values(e2,r,'charge.success');
 perform apply_test_payment(r,e2,111,'paid');
 if (select count(*) from paystack_test_entitlements where user_id=u)<>1
 or (select expires_at from paystack_test_entitlements where reference=r)<>original_expiry then
 raise exception 'Duplicate extended access'; end if;

 insert into paystack_test_events(digest,reference,event_type) values(e3,r2,'charge.success');
 begin
  perform apply_test_payment(r2,e3,111,'paid');
  raise exception 'Transaction reused';
 exception when unique_violation then null; end;
 perform apply_test_payment(r2,e3,222,'paid');
 if (select starts_at from paystack_test_entitlements where reference=r2)<>original_expiry then
 raise exception 'Renewal overlaps'; end if;
 insert into paystack_test_events(digest,reference,event_type) values(repeat('d',64),r,'refund.processed');
 perform apply_test_payment(r,repeat('d',64),111,'refunded');
 insert into paystack_test_events(digest,reference,event_type) values(repeat('e',64),r,'charge.success');
 perform apply_test_payment(r,repeat('e',64),111,'paid');
 if (select state from paystack_test_entitlements where reference=r)<>'revoked' then
 raise exception 'Refund reversed by late success'; end if;
 if test_premium_active(u) then raise exception 'Future grant incorrectly active'; end if;

 -- Out-of-order refund: a later success must not create any entitlement.
 insert into paystack_test_events(digest,reference,event_type) values(repeat('f',64),r3,'refund.processed');
 perform apply_test_payment(r3,repeat('f',64),333,'refunded');
 insert into paystack_test_events(digest,reference,event_type) values(repeat('1',64),r3,'charge.success');
 perform apply_test_payment(r3,repeat('1',64),333,'paid');
 if exists(select 1 from paystack_test_entitlements where reference=r3) then raise exception 'Refund-before-success granted access'; end if;

 -- Expiry is evaluated on reads, with no scheduler dependence.
 update paystack_test_entitlements set starts_at=now()-interval '2 months',expires_at=now()-interval '1 month' where reference=r2;
 if test_premium_active(u) then raise exception 'Expired grant active'; end if;
 -- A future renewal anchored on January 31 clamps to February's final day.
 update paystack_test_entitlements set starts_at='2099-01-01 UTC',expires_at='2099-01-31 12:00 UTC' where reference=r2;
 insert into paystack_test_intents(reference,user_id,request_key,amount_kobo) values(r4,u,'four',300000);
 insert into paystack_test_events(digest,reference,event_type) values(repeat('2',64),r4,'charge.success');
 perform apply_test_payment(r4,repeat('2',64),444,'paid');
 if (select expires_at from paystack_test_entitlements where reference=r4)<>'2099-02-28 12:00 UTC'::timestamptz then
 raise exception 'Month-end renewal incorrect'; end if;
 insert into paystack_test_events(digest,reference,event_type) values(repeat('3',64),r4,'charge.dispute.create');
 perform apply_test_payment(r4,repeat('3',64),444,'suspended');
 insert into paystack_test_events(digest,reference,event_type) values(repeat('4',64),r4,'charge.success');
 perform apply_test_payment(r4,repeat('4',64),444,'paid');
 if (select state from paystack_test_entitlements where reference=r4)<>'suspended' then
 raise exception 'Dispute reversed by late success'; end if;
 if has_table_privilege('anon','paystack_test_intents','SELECT') or
 has_function_privilege('authenticated','apply_test_payment(text,text,bigint,text)','EXECUTE') then
 raise exception 'Payment records exposed'; end if;
end $$;
rollback;

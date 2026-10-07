begin;
do $$
declare u uuid := gen_random_uuid(); r uuid := gen_random_uuid(); a jsonb; b jsonb;
begin
 insert into public.users(id,telegram_id,display_name) values(u,-1000000001,'rollback-test');
 a := public.finish_student_practice(u,'test-session','units',3,2,current_date);
 b := public.finish_student_practice(u,'test-session','units',3,2,current_date);
 if not (b->>'already_completed')::boolean
 or (select count(*) from public.quiz_results where user_id=u)<>1
 or (select count(*) from public.study_activity where user_id=u)<>1 then
  raise exception 'Duplicate completion'; end if;
 if not public.reserve_ai_spend(r,u,'response','test-model',100,100,100,100) then raise exception 'Reservation failed'; end if;
 if public.reserve_ai_spend(r,u,'response','test-model',100,100,100,100) then raise exception 'Replay allowed'; end if;
 if public.reserve_ai_spend(gen_random_uuid(),u,'response','test-model',1,100,100,100) then raise exception 'Budget exceeded'; end if;
 perform public.settle_ai_spend(r,40,10,5,false);
 perform public.settle_ai_spend(r,0,0,0,false);
 if (select charged_micros from public.ai_spend_ledger where id=r)<>40 then raise exception 'Settlement replay'; end if;
 if not public.reserve_ai_spend(gen_random_uuid(),u,'response','test-model',60,100,100,100) then raise exception 'Allowance not released'; end if;
 if has_function_privilege('anon','public.reserve_ai_spend(uuid,uuid,text,text,bigint,bigint,bigint,bigint)','EXECUTE') then raise exception 'Anonymous RPC'; end if;
 if has_table_privilege('authenticated','public.student_practice_answers','SELECT') then raise exception 'Private content exposed'; end if;
end $$;
rollback;

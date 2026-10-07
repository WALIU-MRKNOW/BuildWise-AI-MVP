"""Run only against the disposable CI PostgreSQL service, never production."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
from uuid import uuid4


def sql(query):
    return subprocess.run(['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-Atc', query],
                          check=True, text=True, capture_output=True).stdout.strip()


if __name__ == '__main__':
    if os.getenv('BUILDWISE_DISPOSABLE_DB_TESTS') != '1' or os.getenv('PGHOST') != '127.0.0.1':
        raise SystemExit('A disposable local CI database is required')
    user = str(uuid4())
    sql(f"insert into users(id,telegram_id,display_name) values('{user}',-2000000001,'CI concurrency')")

    def reserve(_):
        return sql(f"select reserve_ai_spend('{uuid4()}','{user}','response','test-model',30,100,100,100)")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, range(10)))
    assert results.count('t') == 3, results
    assert sql("select sum(charged_micros) from ai_spend_ledger") == '90'

    def finish(_):
        return sql(f"select finish_student_practice('{user}','same-session','units',3,2,current_date)")

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(finish, range(10)))
    assert sql(f"select count(*) from quiz_results where user_id='{user}'") == '1'
    assert sql(f"select count(*) from study_activity where user_id='{user}'") == '1'
    print('Concurrent spend reservations and quiz completion passed')

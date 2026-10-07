"""Owned, bounded student records; analytics uses only aggregate SQL output."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bot.services.reward_usage_service import StudentAccessSupabaseService
from bot.services.supabase_service import local_date, SupabaseError


def recommendations(rows):
    totals = defaultdict(lambda: [0, 0])
    for row in rows:
        topic = row['topic']
        totals[topic][0] += int(row['correct'])
        totals[topic][1] += 1
    return sorted(
        ((topic, correct, total) for topic, (correct, total) in totals.items()),
        key=lambda item: (item[1] / item[2], -item[2], item[0]),
    )[:5]


class StudentToolsDatabase(StudentAccessSupabaseService):
    async def record_practice_answer(self, profile, session, selected):
        question = session['questions'][session['index']]
        # Respect history preference: keep accuracy metadata, omit private work.
        keep = profile.save_history
        rows = await self._request('POST', '/rest/v1/student_practice_answers',
            params={'on_conflict': 'user_id,session_id,question_index'},
            headers={'Prefer': 'resolution=ignore-duplicates,return=representation'},
            json={'user_id': profile.id, 'session_id': session['id'],
                  'question_index': session['index'], 'topic': session['topic'][:200],
                  'correct': selected == question.correct_index,
                  'selected_index': selected,
                  'question': question.question[:2000] if keep else '',
                  'answer': question.options[question.correct_index][:1000] if keep else '',
                  'explanation': question.explanation[:3000] if keep else ''})
        if not rows:
            rows = await self._request('GET', '/rest/v1/student_practice_answers', params={
                'user_id': f'eq.{profile.id}', 'session_id': f"eq.{session['id']}",
                'question_index': f"eq.{session['index']}", 'select': 'selected_index', 'limit': '1'})
        if not rows:
            raise SupabaseError('Practice answer could not be saved')
        return int(rows[0]['selected_index'])

    async def finish_practice(self, profile, session):
        return await self._rpc('finish_student_practice', {
            'p_user_id': profile.id, 'p_session_id': session['id'],
            'p_topic': session['topic'][:200], 'p_total': len(session['questions']),
            'p_correct': session['correct'], 'p_date': local_date(profile.timezone).isoformat()})

    async def practice_review(self, user_id):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        return await self._request('GET', '/rest/v1/student_practice_answers', params={
            'user_id': f'eq.{user_id}', 'created_at': f'gte.{cutoff}',
            'select': 'topic,correct,question,answer,explanation',
            'order': 'created_at.desc', 'limit': '200'})

    async def optional_profile(self, user_id, *, programme=None, level=None):
        if programme is None:
            return await self._request('GET', '/rest/v1/student_optional_profiles',
                params={'user_id': f'eq.{user_id}', 'select': 'programme,level', 'limit': '1'})
        await self._request('POST', '/rest/v1/student_optional_profiles',
            params={'on_conflict': 'user_id'},
            headers={'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json={'user_id': user_id, 'programme': programme, 'level': level})

    async def save_feedback(self, user_id, answer_id, rating):
        if rating not in {'helpful', 'unhelpful', 'issue'}:
            raise ValueError('Invalid feedback')
        await self._request('POST', '/rest/v1/student_feedback',
            params={'on_conflict': 'user_id,answer_id'},
            headers={'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json={'user_id': user_id, 'answer_id': answer_id, 'rating': rating})

    async def delete_history(self, user_id):
        count = await super().delete_history(user_id)
        if self._settings.student_tools_enabled:
            await self._request('DELETE', '/rest/v1/student_practice_answers',
                                params={'user_id': f'eq.{user_id}'})
        return count

    async def prune_practice_review(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await self._request('DELETE', '/rest/v1/student_practice_answers',
                            params={'created_at': f'lt.{cutoff}'})

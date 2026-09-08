"""Async Supabase Data API and private Storage adapter for BuildWise."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from bot.config import Settings
from bot.domain import (
    DashboardStats,
    DueReminder,
    StudyCompletion,
    UsageStatus,
    UserProfile,
    WeeklyProgress,
)


class SupabaseError(RuntimeError):
    pass


def validate_timezone(value: str) -> str:
    cleaned = value.strip()
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            "Use a timezone such as Africa/Lagos, Europe/London, or America/Chicago."
        ) from exc
    return cleaned


def local_date(timezone_name: str, moment: datetime | None = None) -> date:
    current = moment or datetime.now(timezone.utc)
    return current.astimezone(ZoneInfo(timezone_name)).date()


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _auth_headers(key: str) -> dict[str, str]:
    """Build headers for new revocable secrets and legacy JWT keys."""
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


class SupabaseService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        key = self._settings.supabase_secret_key.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=self._settings.supabase_url,
            headers=_auth_headers(key),
            timeout=httpx.Timeout(30.0, connect=15.0),
        )
        await self.healthcheck()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise SupabaseError("Supabase has not been initialized")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        try:
            response = await self._http().request(
                method,
                path,
                params=params,
                json=json,
                content=content,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise SupabaseError("The database request timed out") from exc
        except httpx.HTTPError as exc:
            raise SupabaseError("The database could not be reached") from exc
        if allow_not_found and response.status_code == 404:
            return None
        if response.is_error:
            raise SupabaseError(
                f"Supabase request failed with status {response.status_code}"
            )
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.json()
        return response.content

    async def _rpc(self, function: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", f"/rest/v1/rpc/{function}", json=payload)

    async def healthcheck(self) -> None:
        await self._request(
            "GET", "/rest/v1/users", params={"select": "id", "limit": "1"}
        )

    async def claim_update(self, update_id: int) -> bool:
        result = await self._rpc("claim_telegram_update", {"p_update_id": update_id})
        return bool(result)

    async def upsert_user(
        self, telegram_id: int, username: str | None, display_name: str
    ) -> UserProfile:
        rows = await self._rpc(
            "upsert_telegram_user",
            {
                "p_telegram_id": telegram_id,
                "p_username": username,
                "p_display_name": display_name,
            },
        )
        if not rows:
            raise SupabaseError("Could not create or load the user profile")
        return UserProfile.from_row(rows[0])

    async def get_user(self, user_id: str) -> UserProfile:
        rows = await self._request(
            "GET",
            "/rest/v1/users",
            params={"id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )
        if not rows:
            raise SupabaseError("User profile was not found")
        return UserProfile.from_row(rows[0])

    async def update_user(self, user_id: str, values: dict[str, Any]) -> UserProfile:
        safe_values = dict(values)
        safe_values["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = await self._request(
            "PATCH",
            "/rest/v1/users",
            params={"id": f"eq.{user_id}"},
            json=safe_values,
            headers={"Prefer": "return=representation"},
        )
        if not rows:
            raise SupabaseError("User settings could not be updated")
        return UserProfile.from_row(rows[0])

    async def complete_onboarding(
        self,
        user_id: str,
        *,
        student_name: str,
        university: str,
    ) -> UserProfile:
        cleaned_name = " ".join(student_name.split())
        cleaned_university = " ".join(university.split())
        if not 2 <= len(cleaned_name) <= 100:
            raise ValueError("Name must be between 2 and 100 characters.")
        if not 2 <= len(cleaned_university) <= 160:
            raise ValueError("Institution must be between 2 and 160 characters.")
        return await self.update_user(
            user_id,
            {
                "student_name": cleaned_name,
                "university": cleaned_university,
                "onboarding_completed": True,
            },
        )

    async def record_activity_event(
        self,
        *,
        user_id: str,
        event_type: str,
        mode: str | None = None,
        detail: str | None = None,
    ) -> None:
        cleaned_type = " ".join(event_type.split())[:80]
        cleaned_detail = " ".join(detail.split())[:500] if detail else None
        if not cleaned_type:
            raise ValueError("Activity event type cannot be blank")
        await self._request(
            "POST",
            "/rest/v1/user_activity_events",
            json={
                "user_id": user_id,
                "event_type": cleaned_type,
                "mode": mode,
                "detail": cleaned_detail,
            },
            headers={"Prefer": "return=minimal"},
        )

    async def mark_timezone_prompted(self, user_id: str) -> None:
        await self.update_user(user_id, {"timezone_prompted": True})

    async def set_timezone(self, user_id: str, timezone_name: str) -> UserProfile:
        validated = validate_timezone(timezone_name)
        profile = await self.update_user(
            user_id,
            {
                "timezone": validated,
                "timezone_confirmed": True,
                "timezone_prompted": True,
            },
        )
        await self._request(
            "POST",
            "/rest/v1/notification_preferences",
            params={"on_conflict": "user_id"},
            json={"user_id": user_id, "timezone": validated},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return profile

    async def toggle_user_setting(self, profile: UserProfile, setting: str) -> UserProfile:
        values: dict[str, Any]
        if setting == "style":
            values = {
                "answer_style": (
                    "detailed" if profile.answer_style.value == "concise" else "concise"
                )
            }
        elif setting == "buttons":
            values = {"follow_up_buttons": not profile.follow_up_buttons}
        elif setting == "history":
            values = {"save_history": not profile.save_history}
        else:
            raise ValueError("Unknown setting")
        return await self.update_user(profile.id, values)

    async def get_dashboard(self, profile: UserProfile) -> DashboardStats:
        today = local_date(profile.timezone)
        await self._request(
            "POST",
            "/rest/v1/daily_goals",
            params={"on_conflict": "user_id,date"},
            json={
                "user_id": profile.id,
                "date": today.isoformat(),
                "target": profile.default_daily_goal,
                "completed_today": 0,
            },
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
        streak_rows = await self._request(
            "GET",
            "/rest/v1/streaks",
            params={"user_id": f"eq.{profile.id}", "select": "*", "limit": "1"},
        )
        goal_rows = await self._request(
            "GET",
            "/rest/v1/daily_goals",
            params={
                "user_id": f"eq.{profile.id}",
                "date": f"eq.{today.isoformat()}",
                "select": "target,completed_today",
                "limit": "1",
            },
        )
        streak = streak_rows[0] if streak_rows else {}
        goal = goal_rows[0] if goal_rows else {}
        last_activity = _parse_date(streak.get("last_activity_date"))
        current = int(streak.get("current_streak", 0))
        if last_activity is not None and last_activity < today - timedelta(days=1):
            current = 0
        return DashboardStats(
            current_streak=current,
            longest_streak=int(streak.get("longest_streak", 0)),
            total_study_days=int(streak.get("total_study_days", 0)),
            completed_today=int(goal.get("completed_today", 0)),
            daily_target=int(goal.get("target", profile.default_daily_goal)),
            last_activity_date=last_activity,
        )

    async def set_daily_goal(self, profile: UserProfile, target: int) -> DashboardStats:
        if target < 1 or target > 100:
            raise ValueError("Daily goal must be between 1 and 100")
        profile = await self.update_user(profile.id, {"default_daily_goal": target})
        today = local_date(profile.timezone)
        await self._request(
            "POST",
            "/rest/v1/daily_goals",
            params={"on_conflict": "user_id,date"},
            json={"user_id": profile.id, "date": today.isoformat(), "target": target},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return await self.get_dashboard(profile)

    async def consume_usage(self, profile: UserProfile, limit: int) -> UsageStatus:
        rows = await self._rpc(
            "consume_daily_usage",
            {
                "p_user_id": profile.id,
                "p_usage_date": local_date(profile.timezone).isoformat(),
                "p_limit": limit,
            },
        )
        row = rows[0] if isinstance(rows, list) else rows
        return UsageStatus(bool(row["allowed"]), int(row["used"]), int(row["limit_value"]))

    async def refund_usage(self, profile: UserProfile) -> None:
        await self._rpc(
            "refund_daily_usage",
            {
                "p_user_id": profile.id,
                "p_usage_date": local_date(profile.timezone).isoformat(),
            },
        )

    async def record_study_completion(
        self,
        profile: UserProfile,
        *,
        activity_type: str,
        subject: str | None,
        topic: str | None,
        units: int = 1,
    ) -> StudyCompletion:
        rows = await self._rpc(
            "record_study_completion",
            {
                "p_user_id": profile.id,
                "p_activity_type": activity_type,
                "p_subject": subject,
                "p_topic": topic,
                "p_local_date": local_date(profile.timezone).isoformat(),
                "p_units": max(1, units),
            },
        )
        row = rows[0] if isinstance(rows, list) else rows
        milestone_value = int(row["milestone"]) if row.get("milestone") else None
        return StudyCompletion(
            current_streak=int(row["current_streak"]),
            longest_streak=int(row["longest_streak"]),
            completed_today=int(row["completed_today"]),
            daily_target=int(row["daily_target"]),
            milestone=milestone_value,
            goal_just_completed=bool(row.get("goal_just_completed", False)),
        )

    async def save_conversation(
        self,
        *,
        user_id: str,
        mode: str,
        message: str,
        response: str,
        category: str | None,
        file_reference: str | None,
    ) -> str:
        rows = await self._request(
            "POST",
            "/rest/v1/conversations",
            json={
                "user_id": user_id,
                "mode": mode,
                "message": message[:16000],
                "response": response[:24000],
                "category": category,
                "file_reference": file_reference,
            },
            headers={"Prefer": "return=representation"},
        )
        return str(rows[0]["id"])

    async def recent_conversations(
        self, user_id: str, *, mode: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        params = {
            "user_id": f"eq.{user_id}",
            "select": "id,mode,message,response,category,file_reference,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if mode:
            params["mode"] = f"eq.{mode}"
        return await self._request("GET", "/rest/v1/conversations", params=params)

    async def save_item(
        self,
        *,
        user_id: str,
        item_type: str,
        title: str,
        content: str,
        file_reference: str | None,
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/saved_items",
            json={
                "user_id": user_id,
                "item_type": item_type,
                "title": title[:160],
                "content": content[:24000],
                "file_reference": file_reference,
            },
            headers={"Prefer": "return=minimal"},
        )

    async def recent_saved_items(self, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "select": "id,item_type,title,content,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def delete_history(self, user_id: str) -> int:
        conversations = await self.recent_conversations(user_id, limit=500)
        references = {
            row.get("file_reference") for row in conversations if row.get("file_reference")
        }
        for reference in references:
            try:
                await self.delete_file(str(reference))
            except SupabaseError:
                pass
        await self._request(
            "DELETE", "/rest/v1/conversations", params={"user_id": f"eq.{user_id}"}
        )
        await self._request(
            "DELETE", "/rest/v1/saved_items", params={"user_id": f"eq.{user_id}"}
        )
        return len(conversations)

    async def upload_file(
        self,
        *,
        user_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> str:
        suffix = Path(filename).suffix.lower()
        if not suffix or len(suffix) > 10:
            suffix = ".bin"
        stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        object_path = f"{user_id}/{stamp}/{uuid4().hex}{suffix}"
        encoded_path = quote(object_path, safe="/")
        await self._request(
            "POST",
            f"/storage/v1/object/{self._settings.supabase_storage_bucket}/{encoded_path}",
            content=data,
            headers={"Content-Type": mime_type, "x-upsert": "false"},
        )
        return object_path

    async def download_file(self, object_path: str) -> bytes:
        encoded_path = quote(object_path, safe="/")
        result = await self._request(
            "GET",
            f"/storage/v1/object/{self._settings.supabase_storage_bucket}/{encoded_path}",
        )
        if not isinstance(result, bytes):
            raise SupabaseError("Stored file could not be downloaded")
        return result

    async def delete_file(self, object_path: str) -> None:
        encoded_path = quote(object_path, safe="/")
        await self._request(
            "DELETE",
            f"/storage/v1/object/{self._settings.supabase_storage_bucket}/{encoded_path}",
            allow_not_found=True,
        )

    async def set_reminder(
        self,
        profile: UserProfile,
        *,
        enabled: bool,
        reminder_time: str | None = None,
        reminder_type: str = "streak",
    ) -> None:
        payload: dict[str, Any] = {
            "user_id": profile.id,
            "enabled": enabled,
            "timezone": profile.timezone,
            "reminder_type": reminder_type,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if reminder_time is not None:
            time.fromisoformat(reminder_time)
            payload["reminder_time"] = reminder_time
        await self._request(
            "POST",
            "/rest/v1/notification_preferences",
            params={"on_conflict": "user_id"},
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    async def disable_reminder_by_user_id(self, user_id: str) -> None:
        await self._request(
            "PATCH",
            "/rest/v1/notification_preferences",
            params={"user_id": f"eq.{user_id}"},
            json={
                "enabled": False,
                "snooze_until": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def snooze_reminder(self, user_id: str, hours: int = 2) -> None:
        await self._request(
            "PATCH",
            "/rest/v1/notification_preferences",
            params={"user_id": f"eq.{user_id}"},
            json={
                "enabled": True,
                "last_reminder_date": None,
                "snooze_until": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def due_reminders(self) -> list[DueReminder]:
        rows = await self._rpc(
            "get_due_reminders", {"p_now": datetime.now(timezone.utc).isoformat()}
        )
        return [
            DueReminder(
                user_id=str(row["user_id"]),
                telegram_id=int(row["telegram_id"]),
                local_date=date.fromisoformat(row["local_date"]),
                current_streak=int(row["current_streak"]),
                daily_target=int(row["daily_target"]),
                daily_completed=int(row["daily_completed"]),
                reminder_type=row.get("reminder_type") or "streak",
            )
            for row in rows or []
        ]

    async def mark_reminder_sent(self, user_id: str, sent_date: date) -> None:
        await self._request(
            "PATCH",
            "/rest/v1/notification_preferences",
            params={"user_id": f"eq.{user_id}"},
            json={
                "last_reminder_date": sent_date.isoformat(),
                "snooze_until": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def record_quiz_result(
        self,
        *,
        user_id: str,
        subject: str | None,
        topic: str,
        total_questions: int,
        correct_answers: int,
    ) -> None:
        score = round(100 * correct_answers / max(1, total_questions), 2)
        await self._request(
            "POST",
            "/rest/v1/quiz_results",
            json={
                "user_id": user_id,
                "subject": subject,
                "topic": topic,
                "total_questions": total_questions,
                "correct_answers": correct_answers,
                "score": score,
            },
            headers={"Prefer": "return=minimal"},
        )

    async def weekly_progress(self, profile: UserProfile) -> WeeklyProgress:
        now = datetime.now(timezone.utc)
        tz = ZoneInfo(profile.timezone)
        local_today = now.astimezone(tz).date()
        local_start = datetime.combine(
            local_today - timedelta(days=6), time.min, tzinfo=tz
        ).astimezone(timezone.utc)
        since = local_start.isoformat()
        activities = await self._request(
            "GET",
            "/rest/v1/study_activity",
            params={
                "user_id": f"eq.{profile.id}",
                "created_at": f"gte.{since}",
                "completed": "eq.true",
                "select": "activity_type,topic,units,created_at",
            },
        )
        quizzes = await self._request(
            "GET",
            "/rest/v1/quiz_results",
            params={
                "user_id": f"eq.{profile.id}",
                "created_at": f"gte.{since}",
                "select": "topic,total_questions,correct_answers,score,created_at",
            },
        )
        dashboard = await self.get_dashboard(profile)
        topic_counts = Counter(
            row["topic"] for row in activities if row.get("topic")
        )
        topic_scores: dict[str, list[float]] = defaultdict(list)
        for row in quizzes:
            if row.get("topic"):
                topic_scores[str(row["topic"])].append(float(row["score"]))
        total_questions = sum(int(row["total_questions"]) for row in quizzes)
        total_correct = sum(int(row["correct_answers"]) for row in quizzes)
        accuracy = (
            round(100 * total_correct / total_questions, 1) if total_questions else None
        )
        study_dates = {
            _parse_datetime(row["created_at"]).astimezone(tz).date()
            for row in activities
        }
        weakest_topic = None
        if topic_scores:
            weakest_topic = min(
                topic_scores,
                key=lambda topic: sum(topic_scores[topic]) / len(topic_scores[topic]),
            )
        return WeeklyProgress(
            questions_solved=sum(int(row.get("units", 1)) for row in activities),
            practice_completed=sum(
                1 for row in activities if row.get("activity_type") in {"quiz", "practice"}
            ),
            accuracy=accuracy,
            current_streak=dashboard.current_streak,
            longest_streak=dashboard.longest_streak,
            most_studied_topic=topic_counts.most_common(1)[0][0] if topic_counts else None,
            weakest_topic=weakest_topic,
            study_days=len(study_dates),
        )

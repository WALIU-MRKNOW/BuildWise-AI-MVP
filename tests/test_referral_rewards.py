import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.domain import UserProfile
from bot.services.reward_usage_service import RewardAwareSupabaseService


def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


def test_profile() -> UserProfile:
    return UserProfile.from_row(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "telegram_id": 123,
            "username": "student",
            "display_name": "Student",
            "timezone": "UTC",
            "timezone_confirmed": True,
            "timezone_prompted": True,
            "answer_style": "concise",
            "follow_up_buttons": True,
            "save_history": True,
            "default_daily_goal": 5,
        }
    )


class RewardUsageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_consume_tracks_reservation_and_bonus_balance(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._rpc = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "allowed": True,
                    "used": 5,
                    "limit_value": 5,
                    "reservation_id": "00000000-0000-0000-0000-000000000099",
                    "source": "referral_credit",
                    "bonus_credits_remaining": 9,
                }
            ]
        )
        usage = await service.consume_usage(test_profile(), 5)
        self.assertTrue(usage.allowed)
        self.assertEqual(usage.source, "referral_credit")
        self.assertEqual(usage.bonus_credits_remaining, 9)
        self.assertIsNotNone(usage.reservation_id)

    async def test_failed_request_refunds_exact_reservation(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._rpc = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                [
                    {
                        "allowed": True,
                        "used": 1,
                        "limit_value": 5,
                        "reservation_id": "00000000-0000-0000-0000-000000000099",
                        "source": "daily",
                        "bonus_credits_remaining": 10,
                    }
                ],
                True,
            ]
        )
        profile = test_profile()
        await service.consume_usage(profile, 5)
        await service.refund_usage(profile)
        refund_call = service._rpc.await_args_list[1]
        self.assertEqual(refund_call.args[0], "refund_usage_reservation")
        self.assertEqual(
            refund_call.args[1]["p_reservation_id"],
            "00000000-0000-0000-0000-000000000099",
        )

    async def test_success_finalization_uses_reward_rpc(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._rpc = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                [
                    {
                        "allowed": True,
                        "used": 1,
                        "limit_value": 5,
                        "reservation_id": "00000000-0000-0000-0000-000000000099",
                        "source": "daily",
                        "bonus_credits_remaining": 0,
                    }
                ],
                [
                    {
                        "reward_granted": True,
                        "rewarded_user_id": "00000000-0000-0000-0000-000000000002",
                        "credits_awarded": 10,
                    }
                ],
            ]
        )
        await service.consume_usage(test_profile(), 5)
        result = await service.finalize_current_usage()
        self.assertTrue(result["reward_granted"])
        finalize_call = service._rpc.await_args_list[1]
        self.assertEqual(finalize_call.args[0], "finalize_usage_and_reward_referrer")
        self.assertEqual(finalize_call.args[1]["p_reward_credits"], 10)


class ReferralRewardMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path("migrations/003_referral_rewards.sql").read_text().lower()

    def test_reward_is_unique_per_referred_user(self) -> None:
        self.assertIn("unique (referred_user_id)", self.sql)
        self.assertIn("on conflict (referred_user_id) do nothing", self.sql)

    def test_self_reward_is_forbidden(self) -> None:
        self.assertIn("check (referrer_user_id <> referred_user_id)", self.sql)
        self.assertIn("v_referrer_id = v_user_id", self.sql)

    def test_daily_allowance_is_attempted_before_bonus_credit(self) -> None:
        daily_pos = self.sql.index("update public.usage_daily")
        credit_pos = self.sql.index("update public.referral_credit_balances")
        self.assertLess(daily_pos, credit_pos)

    def test_reward_requires_success_finalization(self) -> None:
        self.assertIn("finalize_usage_and_reward_referrer", self.sql)
        self.assertIn("where id = p_reservation_id\n       and finalized = false", self.sql)

    def test_reward_tables_keep_rls_and_backend_only_access(self) -> None:
        for table in (
            "referral_rewards",
            "referral_credit_balances",
            "usage_reservations",
        ):
            self.assertIn(f"alter table public.{table} enable row level security", self.sql)
            self.assertIn(f"revoke all on table public.{table} from anon, authenticated", self.sql)
        self.assertIn("grant execute on function public.finalize_usage_and_reward_referrer", self.sql)
        self.assertIn("to service_role", self.sql)

    def test_bonus_refund_restores_credit_and_spend_counter(self) -> None:
        self.assertIn("available_credits = available_credits + 1", self.sql)
        self.assertIn("lifetime_spent = greatest(lifetime_spent - 1, 0)", self.sql)


if __name__ == "__main__":
    unittest.main()

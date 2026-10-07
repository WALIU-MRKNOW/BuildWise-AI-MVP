import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx

from bot.services.ai_metering import UsageMeter, BudgetError, ai_user_id


class MeterTests(unittest.IsolatedAsyncioTestCase):
    def meter(self):
        meter = UsageMeter(SimpleNamespace(ai_budget_enabled=True,
            ai_model_prices_json=json.dumps({'test-model': {'input_usd_per_million': 2,
                'output_usd_per_million': 8, 'max_input_tokens': 1000}}),
            ai_daily_budget_micros=1000000, ai_monthly_budget_micros=2000000,
            ai_user_daily_budget_micros=100000))
        meter._rpc = AsyncMock(side_effect=[True, None])
        return meter

    async def test_reserve_before_call_then_settle_actual_usage(self):
        meter = self.meter()
        async def provider(**kwargs):
            self.assertEqual(meter._rpc.await_count, 1)
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=20, output_tokens=10))
        token = ai_user_id.set('owner-id')
        try:
            await meter.call(provider, model='test-model', max_output_tokens=100)
        finally:
            ai_user_id.reset(token)
        calls = meter._rpc.await_args_list
        self.assertEqual(calls[0].args[1]['p_reserve'], 2800)
        self.assertEqual(calls[0].args[1]['p_user_id'], 'owner-id')
        self.assertEqual(calls[1].args[1]['p_charge'], 120)
        self.assertFalse(calls[1].args[1]['p_uncertain'])

    async def test_budget_denial_prevents_provider_call(self):
        meter = self.meter()
        meter._rpc = AsyncMock(return_value=False)
        provider = AsyncMock()
        with self.assertRaises(BudgetError):
            await meter.call(provider, model='test-model', max_output_tokens=100)
        provider.assert_not_awaited()

    async def test_database_failure_fails_closed(self):
        meter = self.meter()
        meter._rpc = AsyncMock(side_effect=httpx.ConnectError('offline'))
        provider = AsyncMock()
        with self.assertRaises(BudgetError):
            await meter.call(provider, model='test-model', max_output_tokens=100)
        provider.assert_not_awaited()

    async def test_timeout_retains_full_reservation(self):
        meter = self.meter()
        with self.assertRaises(TimeoutError):
            await meter.call(AsyncMock(side_effect=TimeoutError()), model='test-model', max_output_tokens=100)
        self.assertEqual(meter._rpc.await_count, 1)

    async def test_missing_usage_retains_conservative_charge(self):
        meter = self.meter()
        await meter.call(AsyncMock(return_value=SimpleNamespace()), model='test-model', max_output_tokens=100)
        self.assertTrue(meter._rpc.await_args.args[1]['p_uncertain'])
        self.assertEqual(meter._rpc.await_args.args[1]['p_charge'], 2800)

    async def test_unconfigured_model_is_rejected(self):
        meter = self.meter()
        provider = AsyncMock()
        with self.assertRaises(BudgetError):
            await meter.call(provider, model='unknown', max_output_tokens=100)
        meter._rpc.assert_not_awaited()
        provider.assert_not_awaited()

    def test_negative_price_is_rejected(self):
        meter = self.meter()
        meter.settings.ai_model_prices_json = '{"bad":{"input_usd_per_million":-1,"output_usd_per_million":10,"max_input_tokens":1}}'
        with self.assertRaises(BudgetError):
            meter.rate('bad', 'response', 100)

    async def test_retries_get_distinct_reservations(self):
        meter = self.meter()
        meter._rpc = AsyncMock(side_effect=[True, None, True, None])
        provider = AsyncMock(return_value=SimpleNamespace())
        for _ in range(2):
            await meter.call(provider, model='test-model', max_output_tokens=100)
        self.assertNotEqual(meter._rpc.await_args_list[0].args[1]['p_id'], meter._rpc.await_args_list[2].args[1]['p_id'])

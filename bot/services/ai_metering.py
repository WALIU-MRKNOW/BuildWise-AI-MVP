"""Reserve a conservative cost before EVERY provider call, including retries.

Prices/context ceilings must be operator-verified for each model. Missing models
fail closed when enabled. Unknown provider outcomes retain their full reservation.
No prompts, files, answers, emails or provider error bodies are recorded.
"""
from contextvars import ContextVar
from decimal import Decimal, ROUND_CEILING, InvalidOperation
import json
import logging
from uuid import uuid4

import httpx

from bot.services.supabase_service import _auth_headers

ai_user_id: ContextVar[str | None] = ContextVar('ai_user_id', default=None)
logger = logging.getLogger(__name__)


class BudgetError(RuntimeError):
    pass


def micros(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError('Invalid cost')
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class UsageMeter:
    def __init__(self, settings):
        self.settings = settings

    async def _rpc(self, name, payload):
        async with httpx.AsyncClient(base_url=self.settings.supabase_url,
                headers=_auth_headers(self.settings.supabase_secret_key.get_secret_value()),
                timeout=15) as client:
            response = await client.post('/rest/v1/rpc/' + name, json=payload)
            response.raise_for_status()
            return response.json() if response.content else None

    def rate(self, model, feature, output_limit):
        try:
            rates = json.loads(self.settings.ai_model_prices_json)
            rate = rates[model]
            if feature == 'image':
                reserve = micros(Decimal(str(rate['image_max_usd'])) * 1_000_000)
            else:
                ceiling = rate['max_input_tokens']
                if type(ceiling) is not int or not 1 <= ceiling <= 4_000_000:
                    raise ValueError('Invalid context ceiling')
                for name in ('input_usd_per_million', 'output_usd_per_million'):
                    if Decimal(str(rate[name])) <= 0 or not Decimal(str(rate[name])).is_finite():
                        raise ValueError('Invalid price')
                if type(output_limit) is not int or not 1 <= output_limit <= 32000:
                    raise ValueError('Output must be bounded')
                reserve = micros(Decimal(str(rate['input_usd_per_million'])) * ceiling
                               + Decimal(str(rate['output_usd_per_million'])) * output_limit)
            if reserve <= 0 or reserve > 1_000_000_000:
                raise ValueError('Invalid reservation')
            return rate, reserve
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise BudgetError('This AI model is awaiting verified cost configuration. Please try again later.') from None

    async def call(self, operation, *, feature='response', **kwargs):
        if not self.settings.ai_budget_enabled:
            return await operation(**kwargs)
        rate, reserve = self.rate(kwargs['model'], feature, kwargs.get('max_output_tokens', 0))
        reservation = str(uuid4())
        try:
            allowed = await self._rpc('reserve_ai_spend', {
                'p_id': reservation, 'p_user_id': ai_user_id.get(), 'p_feature': feature,
                'p_model': kwargs['model'], 'p_reserve': reserve,
                'p_daily': self.settings.ai_daily_budget_micros,
                'p_monthly': self.settings.ai_monthly_budget_micros,
                'p_user_daily': self.settings.ai_user_daily_budget_micros})
        except (httpx.HTTPError, ValueError):
            raise BudgetError('AI cost controls are temporarily unavailable. Please try again later.') from None
        if allowed is not True:
            raise BudgetError('BuildWise has reached its AI spend budget. Please try again after the budget resets.')
        # On timeout/cancellation/provider error the reservation intentionally stays
        # charged: the provider may have processed the request.
        response = await operation(**kwargs)
        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'input_tokens', None)
        output_tokens = getattr(usage, 'output_tokens', None)
        known = feature == 'response' and type(input_tokens) is int and type(output_tokens) is int and input_tokens >= 0 and output_tokens >= 0
        charge = reserve
        if known:
            charge = micros(Decimal(str(rate['input_usd_per_million'])) * input_tokens
                          + Decimal(str(rate['output_usd_per_million'])) * output_tokens)
        try:
            await self._rpc('settle_ai_spend', {'p_id': reservation, 'p_charge': charge,
                'p_input': input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
                'p_output': output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
                'p_uncertain': not known or charge > reserve})
        except (httpx.HTTPError, ValueError):
            # Keep the full reservation; a storage failure must not allow overspend.
            logger.warning('AI usage settlement unavailable; reservation retained')
        return response

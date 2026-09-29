# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Quota enforcement performed before every engine call."""
from __future__ import annotations

from datetime import datetime, time

from odoo.fields import Datetime
from odoo.tools.translate import LazyTranslate

from .engines.errors import UsageLimitReached

_lt = LazyTranslate(__name__)

PARAM_PREFIX = 'ebshel_ai_suite.'


class UsageGuard:
    """Checks the configured limits (``0`` means unlimited):

    * requests per user per day
    * tokens per user per day
    * requests per assistant per day (assistant setting overrides the global one)
    * tokens per company per calendar month
    """

    def __init__(self, env):
        self.env = env

    def _param(self, key: str) -> int:
        value = self.env['ir.config_parameter'].sudo().get_param(PARAM_PREFIX + key, '0')
        try:
            return max(0, int(value or 0))
        except ValueError:
            return 0

    def _totals(self, domain) -> tuple[int, int]:
        rows = self.env['community.ai.usage'].sudo()._read_group(
            domain, aggregates=['__count', 'token_total:sum'])
        count, tokens = rows[0] if rows else (0, 0)
        return count or 0, tokens or 0

    def ensure_allowed(self, assistant=None, connection=None) -> None:
        user = self.env.user
        if self.env.su and user._is_superuser():
            return
        today = datetime.combine(Datetime.now().date(), time.min)
        user_daily_requests = self._param('limit_user_daily_requests')
        user_daily_tokens = self._param('limit_user_daily_tokens')
        if user_daily_requests or user_daily_tokens:
            count, tokens = self._totals([('user_id', '=', user.id), ('requested_at', '>=', today)])
            if user_daily_requests and count >= user_daily_requests:
                raise UsageLimitReached(
                    detail=f'user {user.id} reached {user_daily_requests} requests/day',
                    public_text=_lt('You reached your daily number of AI requests. Please try again tomorrow.'))
            if user_daily_tokens and tokens >= user_daily_tokens:
                raise UsageLimitReached(
                    detail=f'user {user.id} reached {user_daily_tokens} tokens/day',
                    public_text=_lt('You reached your daily AI token allowance. Please try again tomorrow.'))
        if assistant:
            limit = assistant.sudo().daily_request_limit or self._param('limit_assistant_daily_requests')
            if limit:
                count, _tokens = self._totals([('assistant_id', '=', assistant.id), ('requested_at', '>=', today)])
                if count >= limit:
                    raise UsageLimitReached(
                        detail=f'assistant {assistant.id} reached {limit} requests/day',
                        public_text=_lt('This assistant reached its daily request limit. Please try again tomorrow.'))
        monthly = self._param('limit_monthly_tokens')
        if monthly:
            month_start = today.replace(day=1)
            _count, tokens = self._totals([('company_id', '=', self.env.company.id),
                                           ('requested_at', '>=', month_start)])
            if tokens >= monthly:
                raise UsageLimitReached(detail=f'company {self.env.company.id} reached {monthly} tokens/month')

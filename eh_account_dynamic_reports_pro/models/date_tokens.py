# -*- encoding: utf-8 -*-
##############################################################################
#
# ERP Heritage
# Copyright (C) 2026 (https://www.erpheritage.com.au/)
#
##############################################################################
"""
Relative date tokens for stored report options.

Saved views and schedules persist an options dict that is replayed later,
so a literal date range goes stale the day after it is saved. Stored
options may therefore carry a token instead of an ISO date, for example::

    {"date": {"date_from": "auto_prev_month_start",
              "date_to": "auto_prev_month_end"}}

The report handlers only understand ISO dates, so every replay path must
run the options through resolve_relative_dates() first; an unresolved
token reaches fields.Date.from_string() and aborts the render.

Tokens are case insensitive and the "auto_" prefix is optional. The
period-to-date tokens (mtd, qtd, ytd) are key aware: they resolve to the
start of the period under date_from and to today under date_to. ytd and
fiscal_year_* follow the company's fiscal year when a company is given.
Values that are not tokens (ISO dates, other strings) pass through.
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

# Option keys whose string values may carry a relative date token. The walk
# is recursive, so comparison blocks nested under options['date'] resolve too.
DATE_KEYS = frozenset({'date_from', 'date_to', 'date', 'as_of', 'as_of_date'})

_TOKEN_PREFIX = 'auto_'
_START_KEYS = frozenset({'date_from'})


def _quarter_start(day):
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _month_end(day):
    return day.replace(day=1) + relativedelta(months=1) - timedelta(days=1)


def _fiscal_year_bounds(today, company):
    """(start, end) of the fiscal year containing today; calendar fallback."""
    if company:
        try:
            dates = company.sudo().compute_fiscalyear_dates(today)
            start, end = dates.get('date_from'), dates.get('date_to')
            if start and end:
                return start, end
        except Exception:  # noqa: BLE001 - a bad fiscal setup must not break a replay
            pass
    return date(today.year, 1, 1), date(today.year, 12, 31)


def _token_value(token, key, today, company):
    """Return the date a token stands for, or None when it is not a token."""
    month_start = today.replace(day=1)
    quarter_start = _quarter_start(today)
    prev_month_start = month_start - relativedelta(months=1)
    prev_quarter_start = quarter_start - relativedelta(months=3)
    is_start = key in _START_KEYS

    if token == 'today':
        return today
    if token == 'yesterday':
        return today - timedelta(days=1)
    if token == 'month_start':
        return month_start
    if token == 'month_end':
        return _month_end(today)
    if token in ('prev_month_start', 'last_month_start'):
        return prev_month_start
    if token in ('prev_month_end', 'last_month_end'):
        return month_start - timedelta(days=1)
    if token == 'quarter_start':
        return quarter_start
    if token == 'quarter_end':
        return quarter_start + relativedelta(months=3) - timedelta(days=1)
    if token in ('prev_quarter_start', 'last_quarter_start'):
        return prev_quarter_start
    if token in ('prev_quarter_end', 'last_quarter_end'):
        return quarter_start - timedelta(days=1)
    if token == 'year_start':
        return date(today.year, 1, 1)
    if token == 'year_end':
        return date(today.year, 12, 31)
    if token in ('prev_year_start', 'last_year_start'):
        return date(today.year - 1, 1, 1)
    if token in ('prev_year_end', 'last_year_end'):
        return date(today.year - 1, 12, 31)
    if token == 'fiscal_year_start':
        return _fiscal_year_bounds(today, company)[0]
    if token == 'fiscal_year_end':
        return _fiscal_year_bounds(today, company)[1]
    if token == 'mtd':
        return month_start if is_start else today
    if token == 'qtd':
        return quarter_start if is_start else today
    if token == 'ytd':
        return _fiscal_year_bounds(today, company)[0] if is_start else today
    return None


def resolve_token(value, key, today, company=None):
    """Resolve a single option value; non-token values are returned as is."""
    if not isinstance(value, str):
        return value
    token = value.strip().lower()
    if token.startswith(_TOKEN_PREFIX):
        token = token[len(_TOKEN_PREFIX):]
    resolved = _token_value(token, key, today, company)
    return resolved.isoformat() if resolved else value


def resolve_relative_dates(options, today, company=None):
    """Return a deep copy of options with every relative date token resolved.

    :param options: options dict as stored (not mutated).
    :param today: datetime.date the tokens are relative to; callers pass the
        acting user's local day (fields.Date.context_today).
    :param company: optional res.company whose fiscal year drives ytd and
        fiscal_year_* tokens.
    """
    def _walk(node, key=None):
        if isinstance(node, dict):
            return {k: _walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v, key) for v in node]
        if key in DATE_KEYS:
            return resolve_token(node, key, today, company)
        return node

    if not isinstance(options, dict):
        return options
    return _walk(options)

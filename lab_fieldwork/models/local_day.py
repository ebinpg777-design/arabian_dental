# -*- coding: utf-8 -*-
"""Where a day starts, for a lab whose people mostly never set a timezone.

129 of 156 users carry no tz, and the cron runs as __system__, which has none
either. Falling back to UTC put every day boundary 5h30 early: a clinic opened
at 01:00 in Kochi counted towards yesterday, and "is the day over" answered at
half past two in the morning. So the fallback is the company's own timezone,
then the lab's. Never UTC. (2026-09-15)
"""
from datetime import datetime, time

import pytz

LAB_TZ = 'Asia/Kolkata'


def tz_name(env, user=None):
    """The timezone to read a day in: `user`'s own when given, else the
    context's, the current user's, the company's, and finally the lab's."""
    return ((user and user.tz)
            or env.context.get('tz')
            or env.user.tz
            or (user and user.company_id.partner_id.tz)
            or env.company.partner_id.tz
            or LAB_TZ)


def local_midnight_utc(env, day, user=None):
    """Local midnight at the start of `day`, as the naive UTC datetime the
    database stores - what a create_date or check_in is compared with."""
    tz = pytz.timezone(tz_name(env, user))
    start = tz.localize(datetime.combine(day, time.min))
    return start.astimezone(pytz.utc).replace(tzinfo=None)


def local_now(env, user=None):
    """The wall clock where `user` is."""
    return datetime.now(pytz.timezone(tz_name(env, user)))

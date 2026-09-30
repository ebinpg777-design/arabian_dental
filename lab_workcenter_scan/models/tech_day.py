# -*- coding: utf-8 -*-
"""A technician's day, slot by slot.

Three honest signals, strongest first, and never mixed up:

  timers     what Odoo's work-order clock recorded for that person (start/stop on
             the job). Hands on the job. Used for a job whenever it exists.
  bench      accepted -> handed on, from the station board's stamps. Wall clock:
             it includes the tea break. Used for a job only when it has no timer.
  present    the attendance check-in/out where the lab records one; otherwise the
             span from the person's first to last scan of the day.

WHOSE job it is comes from the work order's own two names - the Technician
(`bench_user_id`) and, at a bench that records one, the Finishing Technician
(`finisher_user_id`) - never from who pressed Accept or Hand over on the board.
The lead scans for the whole room, so the accepter is the lead, and the first
version of this screen put every job of the day on the lead's row and left the
technicians at "0 jobs". A job with both names is on both people's rows for the
time it was on the bench, and each of them counts it as a job handed on - both
did it, which is `mrp.workorder._person_credit`'s rule too. The floor and its
KPIs count the JOB, once. A job whose technician was never named is on nobody's
row - it has no person to be busy. (client, 2026-09-30)

A job accepted on an EARLIER day and still not handed on is "carried in": drawn
as held, never counted as busy - a case forgotten on a bench for three days is not
three days of work. The same for a work-order timer somebody started on an earlier
day and never stopped: it is a clock left running, not a month of work, and one
person on the live floor had 129 of them drawing every day as "on jobs since
midnight". OdooBot's automatic time-tracking rows are ignored: they are not a
person.

The one number a lead acts on is "idle while work waited": minutes a technician
was present and not on a job while a job stood arrived-but-not-picked-up at one
of the stations they are named on. A slow technician and an empty queue look the
same on a busy chart; they do not look the same here.

Everything is in the lab's own time (`lab.station._lab_tz`) and minutes are
counted from local midnight, so the screen never does timezone arithmetic.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.tools import SQL

SLOTS = (15, 30, 60, 120)
DAY = 1440
WAIT_LOOKBACK_DAYS = 7          # older arrivals that nobody ever picked up are ghosts
USUAL_WEEKS = 4                 # the "usual" profile: the same weekday, four weeks back
STRIP_DAYS = 14                 # the day strip above the grid


class LabTechDay(models.AbstractModel):
    _name = 'lab.tech.day'
    _description = 'Technician day analysis'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _check_day_access(self):
        if self.env.su:
            return
        if not (self.env.user.has_group('mrp.group_mrp_user')
                or self.env.user.has_group('lab_workcenter_scan.group_production_manager')):
            raise AccessError(_("The technician day is for the production team."))

    @api.model
    def _hours(self):
        """The axis the grid opens on, widened to whatever the day really used."""
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            start = int(ICP.get_param('lab_workcenter_scan.day_start_hour', 8))
            end = int(ICP.get_param('lab_workcenter_scan.day_end_hour', 20))
        except (TypeError, ValueError):
            start, end = 8, 20
        start = min(max(start, 0), 23)
        end = min(max(end, start + 1), 24)
        return start, end

    @staticmethod
    def _minute(dt, origin):
        """Minutes from local midnight (as the naive UTC `origin`) to `dt`, clamped to the day."""
        return int(max(0, min(DAY, (dt - origin).total_seconds() // 60)))

    @staticmethod
    def _fill(arr, a, b, value=1):
        if b > a:
            arr[a:b] = bytes([value]) * (b - a)

    @staticmethod
    def _add(counts, a, b):
        for m in range(a, b):
            counts[m] += 1

    @staticmethod
    def _runs(flags, lo, hi):
        """Longest run of set minutes in flags[lo:hi] as (start, length)."""
        best, cur, start = (lo, 0), 0, lo
        for m in range(lo, hi):
            if flags[m]:
                if cur == 0:
                    start = m
                cur += 1
                if cur > best[1]:
                    best = (start, cur)
            else:
                cur = 0
        return best

    @api.model
    def _hue(self, workcenter_id):
        return (workcenter_id * 47) % 360

    # ------------------------------------------------------------------ reads
    @api.model
    def _bench(self, start, end, since):
        """Jobs on somebody's bench during the day: accepted before it ended and
        handed on after it began (or not yet handed on).

        One row per (job, person): the technician named on the job and, where a
        different person finished it, the finisher too. `uid` is the person the
        row is drawn for; `finisher` is who the hand-over is credited to - the
        finisher where one is named, else the technician - so it is credited once.
        Who pressed Accept on the board is not read at all."""
        return self.env.execute_query_dict(SQL("""
            SELECT wo.id, wo.name, wo.workcenter_id, wo.production_id, mo.name AS mo,
                   so.id AS so_id, so.name AS so,
                   p.uid, wo.bench_user_id AS tech, wo.accepted_at, wo.handed_over_at,
                   COALESCE(wo.finisher_user_id, wo.bench_user_id) AS finisher,
                   wo.state, COALESCE(wo.duration_expected, 0) AS expected,
                   so.patient, so.partner_id, so.priority
              FROM mrp_workorder wo
              JOIN mrp_production mo ON mo.id = wo.production_id
         LEFT JOIN sale_order so ON so.id = mo.sale_id
        CROSS JOIN LATERAL (
                   SELECT wo.bench_user_id AS uid WHERE wo.bench_user_id IS NOT NULL
                   UNION ALL
                   SELECT wo.finisher_user_id WHERE wo.finisher_user_id IS NOT NULL
                      AND wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                   ) p
             WHERE wo.accepted_at IS NOT NULL AND wo.accepted_at < %(end)s AND wo.accepted_at >= %(since)s
               AND (wo.handed_over_at >= %(start)s
                    OR (wo.handed_over_at IS NULL AND wo.state NOT IN ('done', 'cancel')))
        """, start=start, end=end, since=since))

    @api.model
    def _job_names(self, wo_ids):
        """What a work order IS, for a bar that only a timer put on the chart: the
        order, the sale order, the patient and the doctor."""
        if not wo_ids:
            return {}
        rows = self.env.execute_query_dict(SQL("""
            SELECT wo.id, wo.name, wo.production_id, mo.name AS mo, so.id AS so_id, so.name AS so,
                   so.patient, so.partner_id, so.priority, COALESCE(wo.duration_expected, 0) AS expected
              FROM mrp_workorder wo
              JOIN mrp_production mo ON mo.id = wo.production_id
         LEFT JOIN sale_order so ON so.id = mo.sale_id
             WHERE wo.id IN %s
        """, tuple(wo_ids)))
        return {r['id']: r for r in rows}

    @api.model
    def _timers(self, start, end, now):
        root = self.env.ref('base.user_root', raise_if_not_found=False)
        skip = tuple({1, root.id if root else 1})
        return self.env.execute_query_dict(SQL("""
            SELECT p.workorder_id, p.user_id AS uid, p.workcenter_id, p.date_start,
                   COALESCE(p.date_end, %(now)s) AS date_end, p.date_end IS NULL AS open,
                   COALESCE(t.loss_type, 'productive') AS loss_type
              FROM mrp_workcenter_productivity p
         LEFT JOIN mrp_workcenter_productivity_loss l ON l.id = p.loss_id
         LEFT JOIN mrp_workcenter_productivity_loss_type t ON t.id = l.loss_id
             WHERE p.date_start < %(end)s AND COALESCE(p.date_end, %(now)s) > %(start)s
               AND p.user_id IS NOT NULL AND p.user_id NOT IN %(skip)s
        """, start=start, end=end, now=now, skip=skip))

    @api.model
    def _given(self, start, end):
        return self.env.execute_query(SQL("""
            SELECT id, bench_user_id, bench_assigned_at, workcenter_id FROM mrp_workorder
             WHERE bench_assigned_at >= %s AND bench_assigned_at < %s AND bench_user_id IS NOT NULL
        """, start, end))

    @api.model
    def _attendance(self, start, end, now):
        if 'hr.attendance' not in self.env:
            return []
        return self.env.execute_query(SQL("""
            SELECT e.user_id, a.check_in, COALESCE(a.check_out, LEAST(%(now)s, %(end)s))
              FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id
             WHERE e.user_id IS NOT NULL AND a.check_in < %(end)s
               AND COALESCE(a.check_out, %(now)s) > %(start)s
        """, start=start, end=end, now=now))

    @api.model
    def _waiting(self, start, end, now):
        """Arrived-but-not-picked-up intervals per station. A step arrives when the
        step before it is handed on (the first step when the order is made)."""
        since = start - timedelta(days=WAIT_LOOKBACK_DAYS)
        return self.env.execute_query(SQL("""
            WITH touched AS (
                SELECT DISTINCT production_id FROM mrp_workorder
                 WHERE create_date >= %(since)s OR handed_over_at >= %(since)s OR accepted_at >= %(since)s
            ), steps AS (
                SELECT w.id, w.workcenter_id, w.state, w.accepted_at, w.handed_over_at, w.create_date,
                       LAG(w.handed_over_at) OVER (PARTITION BY w.production_id ORDER BY w.sequence, w.id) AS prev_done,
                       LAG(w.id) OVER (PARTITION BY w.production_id ORDER BY w.sequence, w.id) AS prev_id
                  FROM mrp_workorder w WHERE w.production_id IN (SELECT production_id FROM touched)
            )
            SELECT workcenter_id, GREATEST(create_date, COALESCE(prev_done, create_date)) AS arrived,
                   COALESCE(accepted_at, handed_over_at, %(now)s) AS picked
              FROM steps
             WHERE state <> 'cancel' AND (prev_id IS NULL OR prev_done IS NOT NULL)
               AND NOT (state = 'done' AND accepted_at IS NULL AND handed_over_at IS NULL)
               AND GREATEST(create_date, COALESCE(prev_done, create_date)) >= %(since)s
               AND GREATEST(create_date, COALESCE(prev_done, create_date)) < %(end)s
               AND COALESCE(accepted_at, handed_over_at, %(now)s) > %(start)s
        """, since=since, start=start, end=end, now=now))

    @api.model
    def _rostered(self, station_ids=None):
        """Who is named on which station (the Technicians list of each work centre)."""
        where = SQL("TRUE") if not station_ids else SQL("r.workcenter_id IN %s", tuple(station_ids))
        rows = self.env.execute_query(SQL("""
            SELECT r.uid, r.workcenter_id FROM workcenter_users_rel r
              JOIN mrp_workcenter w ON w.id = r.workcenter_id JOIN res_users u ON u.id = r.uid
             WHERE w.active AND u.active AND %s
        """, where)) if self._table_exists('workcenter_users_rel') else []
        out = defaultdict(set)
        for uid, wc in rows:
            out[uid].add(wc)
        return out

    @api.model
    def _table_exists(self, name):
        return bool(self.env.execute_query(SQL("SELECT 1 FROM information_schema.tables WHERE table_name = %s", name)))

    @api.model
    def _usual(self, day, uids, slot, tz):
        """Average hand-overs per slot on the same weekday of the previous weeks."""
        Station = self.env['lab.station']
        windows = [Station._day_window(day - timedelta(weeks=w)) for w in range(1, USUAL_WEEKS + 1)]
        if not uids:
            return {}
        cond = SQL(" OR ").join(SQL("(handed_over_at >= %s AND handed_over_at < %s)", a, b) for a, b in windows)
        # the same person the day credits the hand-over to: the finisher where one
        # is named, else the technician - never who pressed the button
        rows = self.env.execute_query(SQL("""
            SELECT COALESCE(finisher_user_id, bench_user_id) AS uid,
                   (EXTRACT(HOUR FROM (handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %s)) * 60
                    + EXTRACT(MINUTE FROM (handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %s)))::int AS m
              FROM mrp_workorder WHERE (%s) AND COALESCE(finisher_user_id, bench_user_id) IN %s
        """, tz.zone, tz.zone, cond, tuple(uids)))
        usual = defaultdict(lambda: defaultdict(float))
        for uid, m in rows:
            usual[uid][(m // slot) * slot] += 1.0 / USUAL_WEEKS
        return usual

    @api.model
    def _strip(self, day, tz):
        """Hand-overs and people per day, for the fortnight ending on `day`."""
        Station = self.env['lab.station']
        first = day - timedelta(days=STRIP_DAYS - 1)
        a, _b = Station._day_window(first)
        _a, b = Station._day_window(day)
        rows = self.env.execute_query(SQL("""
            SELECT (handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %s)::date AS d, COUNT(*),
                   COUNT(DISTINCT COALESCE(finisher_user_id, bench_user_id))
              FROM mrp_workorder WHERE handed_over_at >= %s AND handed_over_at < %s GROUP BY 1
        """, tz.zone, a, b))
        by_day = {r[0]: (r[1], r[2]) for r in rows}
        out = []
        for i in range(STRIP_DAYS):
            d = first + timedelta(days=i)
            jobs, people = by_day.get(d, (0, 0))
            out.append({'date': fields.Date.to_string(d), 'dow': d.strftime('%a'), 'day': d.day, 'jobs': jobs, 'people': people})
        return out

    # ------------------------------------------------------------------ the day
    @api.model
    def get_day(self, day=None, slot=60, station_ids=None, rostered=False):
        """Everything the screen draws for one day. Minutes count from local midnight."""
        self._check_day_access()
        # everything the SQL below reads - jobs, timers, the MO's order and its
        # patient - or a write still in the cache is invisible to it
        self.env.flush_all()
        Station = self.env['lab.station']
        tz = Station._lab_tz()
        day = fields.Date.to_date(day) if day else Station._lab_today()
        slot = int(slot) if int(slot or 0) in SLOTS else 60
        station_ids = [int(s) for s in (station_ids or [])]
        start, end = Station._day_window(day)
        now = fields.Datetime.now()
        is_today = start <= now < end
        now_min = self._minute(now, start) if is_today else None
        cap = min(now, end)
        since = start - timedelta(days=30)
        h0, h1 = self._hours()

        users = defaultdict(lambda: {
            'segments': [], 'events': [], 'attendance': [], 'busy': bytearray(DAY), 'held': bytearray(DAY), 'active': bytearray(DAY),
            'present': bytearray(DAY), 'stack': [0] * DAY, 'blocked': bytearray(DAY), 'stations': set(),
            'earned': 0.0, 'handed': 0, 'accepted': 0, 'given': 0, 'carried': 0, 'switches': 0})
        wanted = set(station_ids)

        def keep(wc):
            return not wanted or wc in wanted

        bench = self._bench(start, end, since)
        timers = self._timers(start, end, now)
        # the floor counts each JOB once, however many people are named on it
        handed_jobs, accepted_jobs = {}, {}

        def stale(t):
            """A clock started on an earlier day and never stopped."""
            return t['open'] and t['date_start'] < start
        timed = defaultdict(list)
        for t in timers:
            if keep(t['workcenter_id']):
                timed[(t['workorder_id'], t['uid'])].append(t)
        productions = set()
        partners = set()
        for job in bench:
            if not keep(job['workcenter_id']) or not job['uid']:
                continue
            productions.add(job['production_id'])
            if job['partner_id']:
                partners.add(job['partner_id'])
            u = users[job['uid']]
            u['stations'].add(job['workcenter_id'])
            carried = job['accepted_at'] < start and not job['handed_over_at']
            a = self._minute(max(job['accepted_at'], start), start)
            b = self._minute(min(job['handed_over_at'] or cap, end), start)
            own_timers = timed.pop((job['id'], job['uid']), [])
            if carried:
                u['carried'] += 1
                kind = 'carried'
                self._fill(u['held'], a, b)
            elif own_timers:
                kind = 'bench'
                self._fill(u['held'], a, b)
                self._fill(u['active'], a, b)
            else:
                kind = 'bench'
                self._fill(u['busy'], a, b)
                self._fill(u['held'], a, b)
                self._fill(u['active'], a, b)
                self._add(u['stack'], a, b)
            seg = {'wo': job['id'], 'name': job['name'], 'mo': job['mo'], 'so': job['so'] or '', 'so_id': job['so_id'] or False,
                   'production_id': job['production_id'],
                   'station_id': job['workcenter_id'], 'patient': (job['patient'] or '').strip(),
                   'partner_id': job['partner_id'], 'priority': job['priority'] or 'normal',
                   'a': a, 'b': b, 'kind': kind, 'open': not job['handed_over_at'],
                   'expected': round(job['expected'] or 0.0, 1),
                   'accepted': fields.Datetime.to_string(job['accepted_at']),
                   'handed': fields.Datetime.to_string(job['handed_over_at']) if job['handed_over_at'] else None}
            u['segments'].append(seg)
            for t in own_timers:
                ta, tb = self._minute(max(t['date_start'], start), start), self._minute(min(t['date_end'], end), start)
                if stale(t):
                    self._fill(u['held'], ta, tb)
                    u['segments'].append(dict(seg, a=ta, b=tb, kind='carried'))
                    continue
                if t['loss_type'] in ('productive', 'performance'):
                    self._fill(u['busy'], ta, tb)
                    self._add(u['stack'], ta, tb)
                else:
                    self._fill(u['blocked'], ta, tb)
                self._fill(u['active'], ta, tb)
                u['segments'].append(dict(seg, a=ta, b=tb, kind='timer' if t['loss_type'] in ('productive', 'performance') else 'blocked'))
            # each stamp is one event on one row: the accept on the technician's
            # (the finisher's, only when there is no technician), the hand-over on
            # the finisher's - so a job two people share is never counted twice
            if start <= job['accepted_at'] < end and job['uid'] == (job['tech'] or job['finisher']):
                m = self._minute(job['accepted_at'], start)
                u['accepted'] += 1
                accepted_jobs[job['id']] = m
                u['events'].append({'kind': 'accept', 'm': m, 'wo': job['id'],
                                    'station_id': job['workcenter_id'], 'label': job['mo']})
            if job['handed_over_at'] and start <= job['handed_over_at'] < end:
                m = self._minute(job['handed_over_at'], start)
                u['handed'] += 1
                u['earned'] += job['expected'] or 0.0
                handed_jobs[job['id']] = m
                u['events'].append({'kind': 'handed', 'm': m, 'wo': job['id'],
                                    'station_id': job['workcenter_id'], 'label': job['mo']})
        # timers on jobs that are not on anybody's bench (the plain Odoo start/stop) -
        # named all the same, so the bar says which case it was
        named = self._job_names([wo_id for (wo_id, _uid) in timed])
        for (wo_id, uid), rows in timed.items():
            u = users[uid]
            info = named.get(wo_id) or {}
            if info.get('production_id'):
                productions.add(info['production_id'])
            if info.get('partner_id'):
                partners.add(info['partner_id'])
            base = {'wo': wo_id, 'name': info.get('name') or '', 'mo': info.get('mo') or '',
                    'so': info.get('so') or '', 'so_id': info.get('so_id') or False,
                    'production_id': info.get('production_id') or False,
                    'patient': (info.get('patient') or '').strip(), 'partner_id': info.get('partner_id') or False,
                    'priority': info.get('priority') or 'normal', 'expected': round(info.get('expected') or 0.0, 1),
                    'accepted': None, 'handed': None}
            for t in rows:
                ta, tb = self._minute(max(t['date_start'], start), start), self._minute(min(t['date_end'], end), start)
                good = t['loss_type'] in ('productive', 'performance')
                if stale(t):
                    u['carried'] += 1
                    self._fill(u['held'], ta, tb)
                    u['segments'].append(dict(base, station_id=t['workcenter_id'], a=ta, b=tb, kind='carried', open=True))
                    continue
                if good:
                    self._fill(u['busy'], ta, tb)
                    self._add(u['stack'], ta, tb)
                else:
                    self._fill(u['blocked'], ta, tb)
                self._fill(u['active'], ta, tb)
                u['stations'].add(t['workcenter_id'])
                u['segments'].append(dict(base, station_id=t['workcenter_id'], a=ta, b=tb,
                                          kind='timer' if good else 'blocked', open=False))
        for wo_id, uid, at, wc in self._given(start, end):
            if keep(wc):
                users[uid]['given'] += 1
                users[uid]['events'].append({'kind': 'given', 'm': self._minute(at, start), 'wo': wo_id, 'station_id': wc, 'label': ''})
        has_attendance = set()
        for uid, a_in, a_out in self._attendance(start, end, now):
            if uid not in users and wanted:
                continue
            u = users[uid]
            a, b = self._minute(max(a_in, start), start), self._minute(min(a_out, end), start)
            self._fill(u['present'], a, b)
            u['attendance'].append({'a': a, 'b': b})
            has_attendance.add(uid)
        roster = self._rostered(station_ids or None)
        if rostered:
            for uid in roster:
                users[uid]            # touch: the rostered technician with nothing today still gets a row

        # work waiting per station, minute by minute - only at stations that use the
        # board: where nobody ever scans, "not picked up" means "not scanned", not a queue
        scanning = {r[0] for r in self.env.execute_query(SQL(
            "SELECT DISTINCT workcenter_id FROM mrp_workorder WHERE accepted_at >= %s AND accepted_at < %s",
            start - timedelta(days=14), end))}
        waiting = defaultdict(lambda: [0] * DAY)
        arrivals = [0] * DAY
        for wc, arrived, picked in self._waiting(start, end, now):
            if not keep(wc) or wc not in scanning:
                continue
            a, b = self._minute(max(arrived, start), start), self._minute(min(picked, cap if is_today else end), start)
            w = waiting[wc]
            for m in range(a, b):
                w[m] += 1
            if arrived >= start:
                arrivals[self._minute(arrived, start)] += 1

        # the axis: the configured shift, widened to the day's real activity
        lo, hi = h0 * 60, h1 * 60
        for u in users.values():
            for arr in (u['busy'], u['active'], u['present'], u['blocked']):
                first = arr.find(1)
                if first >= 0:
                    lo = min(lo, first)
                    hi = max(hi, arr.rfind(1) + 1)
            for e in u['events']:
                lo, hi = min(lo, e['m']), max(hi, e['m'] + 1)
        lo = (lo // 60) * 60
        hi = min(DAY, -(-hi // 60) * 60)
        lo = (lo // slot) * slot
        slots = list(range(lo, hi, slot))

        names = {u.id: u for u in self.env['res.users'].sudo().browse(list(users)).exists()}
        stations = {w.id: w for w in self.env['mrp.workcenter'].sudo().browse(
            list({s for u in users.values() for s in u['stations']} | set(waiting) | {s for ss in roster.values() for s in ss})).exists()}
        usual = self._usual(day, list(names), slot, tz)
        rows, floor = [], [{'m': s, 'busy_people': 0, 'handed': 0, 'accepted': 0, 'busy_min': 0, 'waiting': 0, 'arrived': 0,
                            'idle_wait': 0} for s in slots]
        for uid, u in users.items():
            user = names.get(uid)
            if not user:
                continue
            events = sorted(u['events'], key=lambda e: e['m'])
            busy, held, present, active = u['busy'], u['held'], u['present'], u['active']
            span = [m for m in (active.find(1),) if m >= 0] + [e['m'] for e in events]
            if uid not in has_attendance and span:
                # no attendance: present from the first scan to the last (a carried-in
                # case alone does not make anybody present)
                first = min(span)
                last = max([active.rfind(1) + 1] + [e['m'] + 1 for e in events])
                self._fill(present, first, max(first + 1, last))
            my_stations = roster.get(uid, set()) | u['stations']
            wait_any = bytearray(DAY)
            for wc in my_stations:
                w = waiting.get(wc)
                if w:
                    for m in range(lo, hi):
                        if w[m]:
                            wait_any[m] = 1
            accepts = [e for e in events if e['kind'] == 'accept']
            u['switches'] = sum(1 for x, y in zip(accepts, accepts[1:]) if x['station_id'] != y['station_id'])
            cells, busy_total, present_total, idle_wait_total = [], 0, 0, 0
            my_usual = usual.get(uid, {})
            for i, s in enumerate(slots):
                e_ = min(s + slot, DAY)
                b_ = sum(busy[s:e_])
                p_ = sum(present[s:e_])
                h_ = sum(held[s:e_])
                blk = sum(u['blocked'][s:e_])
                iw = sum(1 for m in range(s, e_) if present[m] and not busy[m] and not u['blocked'][m] and wait_any[m])
                handed = sum(1 for e in events if e['kind'] == 'handed' and s <= e['m'] < e_)
                acc = sum(1 for e in events if e['kind'] == 'accept' and s <= e['m'] < e_)
                stack = max(u['stack'][s:e_]) if e_ > s else 0
                if blk and not b_:
                    state = 'blocked'
                elif b_ >= (e_ - s) * 0.5:
                    state = 'work'
                elif b_:
                    state = 'part'
                elif iw:
                    state = 'idle_wait'
                elif p_:
                    state = 'idle'
                elif h_:
                    state = 'held'
                else:
                    state = 'off'
                cells.append({'m': s, 'busy': b_, 'present': p_, 'held': h_, 'blocked': blk, 'idle_wait': iw, 'handed': handed,
                              'accepted': acc, 'parallel': stack, 'state': state, 'usual': round(my_usual.get(s, 0.0), 2)})
                busy_total += b_
                present_total += p_
                idle_wait_total += iw
                f = floor[i]
                f['busy_people'] += 1 if b_ else 0
                f['busy_min'] += b_
                f['idle_wait'] += iw
            gap_start, gap_len = self._runs(bytearray(1 if present[m] and not busy[m] else 0 for m in range(DAY)), lo, hi)
            first_act = min([e['m'] for e in events] + [m for m in (busy.find(1),) if m >= 0], default=None)
            last_act = max([e['m'] for e in events] + ([busy.rfind(1) + 1] if busy.find(1) >= 0 else []), default=None)
            rows.append({
                'id': uid, 'name': user.name, 'initials': ''.join(p[:1] for p in (user.name or '?').split()[:2]).upper(),
                'cells': cells, 'segments': sorted(u['segments'], key=lambda s: (s['a'], s['b'])), 'events': events,
                'attendance': u['attendance'], 'has_attendance': uid in has_attendance,
                'stations': sorted(my_stations), 'rostered': uid in roster,
                'busy': busy_total, 'present': present_total, 'idle_wait': idle_wait_total,
                'utilisation': round(busy_total / present_total * 100) if present_total else None,
                'handed': u['handed'], 'accepted': u['accepted'], 'given': u['given'], 'carried': u['carried'],
                'earned': round(u['earned']), 'efficiency': round(u['earned'] / busy_total * 100) if busy_total and u['earned'] else None,
                'switches': u['switches'], 'parallel': max(u['stack'][lo:hi]) if hi > lo else 0,
                'longest_gap': {'m': gap_start, 'len': gap_len} if gap_len >= 30 else None,
                'first': first_act, 'last': last_act,
                'usual_handed': round(sum(my_usual.values()), 1),
                'avg_job': round(busy_total / u['handed']) if u['handed'] else None,
            })
        for i, s in enumerate(slots):
            e_ = min(s + slot, DAY)
            floor[i]['waiting'] = sum(w[e_ - 1] for w in waiting.values())
            floor[i]['arrived'] = sum(arrivals[s:e_])
            floor[i]['handed'] = sum(1 for m in handed_jobs.values() if s <= m < e_)
            floor[i]['accepted'] = sum(1 for m in accepted_jobs.values() if s <= m < e_)
        rows.sort(key=lambda r: (-(r['busy'] + r['handed'] * 5 + r['present'] / 10), r['name']))
        insights = self._insights(rows, floor, slot, stations, waiting, lo, hi)
        partners_map = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(list(partners)).exists()}
        total_present = sum(r['present'] for r in rows)
        total_busy = sum(r['busy'] for r in rows)
        total_earned = sum(r['earned'] for r in rows)
        peak = max(floor, key=lambda f: (f['handed'], f['busy_people']), default=None)
        return {
            'day': fields.Date.to_string(day), 'weekday': day.strftime('%A'), 'is_today': is_today, 'now': now_min,
            'slot': slot, 'slots': slots, 'lo': lo, 'hi': hi, 'tz': tz.zone,
            'rows': rows, 'floor': floor, 'insights': insights, 'strip': self._strip(day, tz),
            'stations': [{'id': w.id, 'name': w.name, 'hue': self._hue(w.id)} for w in sorted(stations.values(), key=lambda w: w.name or '')],
            'station_ids': station_ids, 'partners': partners_map,
            'kpis': {
                'people': sum(1 for r in rows if r['busy'] or r['handed'] or r['accepted']),
                'rostered': len(roster), 'handed': len(handed_jobs), 'accepted': len(accepted_jobs),
                'busy_h': round(total_busy / 60.0, 1), 'present_h': round(total_present / 60.0, 1),
                'utilisation': round(total_busy / total_present * 100) if total_present else None,
                'earned_h': round(total_earned / 60.0, 1),
                'efficiency': round(total_earned / total_busy * 100) if total_busy and total_earned else None,
                'idle_wait_h': round(sum(r['idle_wait'] for r in rows) / 60.0, 1),
                'carried': sum(r['carried'] for r in rows),
                'peak': {'m': peak['m'], 'handed': peak['handed']} if peak and peak['handed'] else None,
                'arrived': sum(f['arrived'] for f in floor),
            },
        }

    @api.model
    def _insights(self, rows, floor, slot, stations, waiting, lo, hi):
        """A few sentences a lead can act on, strongest first."""
        out = []

        def hm(m):
            return '%02d:%02d' % divmod(int(m), 60)
        worst = max(rows, key=lambda r: r['idle_wait'], default=None)
        if worst and worst['idle_wait'] >= 30:
            spots = [c for c in worst['cells'] if c['idle_wait']]
            out.append({'kind': 'warn', 'user_id': worst['id'], 'text': _(
                "%(name)s stood idle for %(h)s while work waited at their stations (around %(when)s).",
                name=worst['name'], h='%d h %02d min' % divmod(worst['idle_wait'], 60) if worst['idle_wait'] >= 60 else '%d min' % worst['idle_wait'],
                when=hm(spots[0]['m']) if spots else '')})
        busy_floor = [f for f in floor if f['handed']]
        if busy_floor:
            peak = max(busy_floor, key=lambda f: f['handed'])
            out.append({'kind': 'info', 'text': _("The floor peaked at %(from)s–%(to)s with %(n)d jobs handed on.",
                                                   **{'from': hm(peak['m']), 'to': hm(peak['m'] + slot), 'n': peak['handed']})})
        staffed = [f for f in floor if f['busy_people']]
        if len(staffed) >= 3:
            dip = min(staffed[1:-1] or staffed, key=lambda f: f['busy_min'] / max(f['busy_people'], 1))
            out.append({'kind': 'muted', 'text': _("The quietest staffed slot was %(from)s–%(to)s.", **{'from': hm(dip['m']), 'to': hm(dip['m'] + slot)})})
        for r in rows:
            if r['usual_handed'] >= 2 and r['handed'] >= r['usual_handed'] * 1.5:
                out.append({'kind': 'good', 'user_id': r['id'], 'text': _("%(name)s handed on %(n)d jobs, well above their usual %(u)s for this weekday.",
                                                                            name=r['name'], n=r['handed'], u=r['usual_handed'])})
            elif r['usual_handed'] >= 3 and r['handed'] <= r['usual_handed'] * 0.5:
                out.append({'kind': 'warn', 'user_id': r['id'], 'text': _("%(name)s handed on %(n)d jobs against a usual %(u)s for this weekday.",
                                                                            name=r['name'], n=r['handed'], u=r['usual_handed'])})
        many = [r for r in rows if r['switches'] >= 4]
        for r in many[:2]:
            out.append({'kind': 'info', 'user_id': r['id'], 'text': _("%(name)s switched station %(n)d times: a lot of walking for one day.", name=r['name'], n=r['switches'])})
        carried = [r for r in rows if r['carried']]
        if carried:
            out.append({'kind': 'warn', 'text': _("%(n)d job(s) were carried in from an earlier day and are still on a bench.", n=sum(r['carried'] for r in carried))})
        return out[:8]

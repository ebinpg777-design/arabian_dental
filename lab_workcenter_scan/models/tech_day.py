# -*- coding: utf-8 -*-
"""A technician's day, slot by slot.

Three honest signals, strongest first, and never mixed up:

  timers     the work order's Time Tracking: what Odoo's clock recorded on the job,
             start/stop. Hands on the job. Used for a job whenever it exists.
  job        the work order's own Start Date -> End Date, as its form shows them
             (client, 2026-10-14: "the gantt chart should be based on the time in
             work order"). Wall clock: it includes the tea break. Used for a job
             only when it has no timer. The board writes both - Accept starts the
             work order, Hand over finishes it - so for a board job they are the
             stamps; a time corrected on the form, or a job started and finished in
             Odoo itself, is drawn as the work order says. The board's stamps are
             only the fallback for a work order with no times of its own.
  present    the attendance check-in/out where the lab records one; otherwise the
             span from the person's first to last scan of the day.

A work order is on the chart only once somebody really worked it - accepted on the
board, or timed by a person. One never started has PLANNED dates only, and core's
finish keeps a planned start that lies before the finish, so an order closed without
its steps ever being started reads as a three-week job (staging: the median such
"job" lasted 20 days). A running job's End Date is core's forecast (start + expected
duration): it is drawn as when the job is due, not as when it ended.

WHOSE job it is comes from the work order's own two names - the Technician
(`bench_user_id`) and, at a bench that records one, the Finishing Technician
(`finisher_user_id`) - never from who pressed Accept or Hand over on the board, and
never from whose name is on a Time Tracking line: the lead who scans for the room
starts every clock, so the clock is the job's, credited to the people named on it.
The lead scans for the whole room, so the accepter is the lead, and the first
version of this screen put every job of the day on the lead's row and left the
technicians at "0 jobs". A job with both names is on both people's rows for the
time it was on the bench, and each of them counts it as a job handed on - both
did it, which is `mrp.workorder._person_credit`'s rule too. The floor and its
KPIs count the JOB, once. A job whose technician was never named is on nobody's
row - it has no person to be busy. (client, 2026-09-30)

A job started on an EARLIER day is "carried in" for the part of it on this day:
drawn as held, never counted as busy - a case forgotten on a bench for three days is
not three days of work, and one started at five yesterday and finished at ten today
is not ten hours of work since midnight. Its finish today still counts as a job
handed on. The same for a work-order timer started on an earlier day: it is a clock
left running, not a month of work, and one person on the live floor had 129 of them
drawing every day as "on jobs since midnight". OdooBot's automatic time-tracking
rows are ignored: they are not a person.

The one number a lead acts on is "idle while work waited": minutes a technician
was present and not on a job while a job stood arrived-but-not-picked-up at one
of the stations they are named on. A slow technician and an empty queue look the
same on a busy chart; they do not look the same here. Idle is counted outside the
lunch on the person's working-hours calendar, and every idle run of five minutes
or more is kept as a period the chart draws (client, 2026-10-14). The same
calendar gives the expected shift, so a late start or an early finish is a fact
and not a feeling.

The rows are the technicians named on a work centre (the roster). Somebody else
with a job is kept aside and named in an insight, never dropped; a rostered
technician with nothing on the day is a name under the chart, not an empty lane.

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
IDLE_PERIOD_MIN = 5             # a gap shorter than this is a glance at the phone, not idle time
LATE_MIN = 15                   # a start this much after the roster's hour is a late start
BURST_N, BURST_MIN = 4, 3       # this many hand-overs inside this many minutes reads as a batch scan
RUSH_RANK = {'emergency': 2, 'urgent': 1}   # emergency outranks urgent (lab_order_control)


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
            start = int(ICP.get_str('lab_workcenter_scan.day_start_hour', 8))
            end = int(ICP.get_str('lab_workcenter_scan.day_end_hour', 20))
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

    @api.model
    def _hue(self, workcenter_id):
        return (workcenter_id * 47) % 360

    # ------------------------------------------------------------------ reads
    @api.model
    def _skip(self):
        """OdooBot and the superuser: their time-tracking lines are automatic, not a person."""
        root = self.env.ref('base.user_root', raise_if_not_found=False)
        return tuple({1, root.id if root else 1})

    @api.model
    def _job_times(self, where):
        """The work orders somebody really worked - accepted on the board, or timed by
        a person - with the times the work order itself states (`where` narrows on
        the alias `wo`, before anything is worked out):

          started   the Start Date once the work order was started (in progress, or
                    done with an end after its start); else the board's accept stamp
          finished  the End Date of a done work order; else the hand-over stamp;
                    NULL while the job runs
          due       a running job's End Date - core's forecast, start + expected

        A done work order whose start IS its end was finished without being started
        (the order closed in one go): it has no time of its own, and is drawn from the
        board's stamps if it has any.

        The candidates are narrowed first and the timed ones looked up by work order:
        written as one `EXISTS` beside an `OR`, the planner costs it at a million and
        PostgreSQL's JIT spends 150 ms compiling an 18 ms query."""
        return SQL("""
            WITH cand AS MATERIALIZED (
                SELECT wo.id, wo.accepted_at FROM mrp_workorder wo
                 WHERE wo.state <> 'cancel' AND (%(where)s)
            ), timed AS MATERIALIZED (
                SELECT DISTINCT t.workorder_id FROM mrp_workcenter_productivity t
                 WHERE t.workorder_id IN (SELECT id FROM cand WHERE accepted_at IS NULL)
                   AND t.user_id NOT IN %(skip)s
            )
            SELECT wo.id,
                   CASE WHEN wo.state = 'progress' AND wo.date_start IS NOT NULL THEN wo.date_start
                        WHEN wo.state = 'done' AND wo.date_finished > wo.date_start THEN wo.date_start
                        ELSE wo.accepted_at END AS started,
                   CASE WHEN wo.state = 'done' AND wo.date_finished > wo.date_start THEN wo.date_finished
                        WHEN wo.state = 'progress' THEN NULL
                        ELSE wo.handed_over_at END AS finished,
                   CASE WHEN wo.state = 'progress' AND wo.date_finished > wo.date_start THEN wo.date_finished END AS due
              FROM cand
              JOIN mrp_workorder wo ON wo.id = cand.id
         LEFT JOIN timed ON timed.workorder_id = cand.id
             WHERE cand.accepted_at IS NOT NULL OR timed.workorder_id IS NOT NULL
        """, where=where, skip=self._skip())

    @api.model
    def _bench(self, start, end, since):
        """Jobs on somebody's bench during the day: started before it ended and
        finished after it began (or not finished yet), by the work order's own times.

        One row per (job, person): the technician named on the job and, where a
        different person finished it, the finisher too. `uid` is the person the
        row is drawn for; `finisher` is who the hand-over is credited to - the
        finisher where one is named, else the technician - so it is credited once.
        Who pressed Accept on the board is not read at all."""
        # cheap first: `started` is one of these two, and must fall in the window
        where = SQL("""(wo.bench_user_id IS NOT NULL OR wo.finisher_user_id IS NOT NULL)
                       AND (wo.date_start >= %(since)s OR wo.accepted_at >= %(since)s)
                       AND (wo.date_start < %(end)s OR wo.accepted_at < %(end)s)""", since=since, end=end)
        return self.env.execute_query_dict(SQL("""
            WITH j AS (%(jobs)s)
            SELECT wo.id, wo.name, wo.workcenter_id, wo.production_id, mo.name AS mo,
                   so.id AS so_id, so.name AS so,
                   p.uid, wo.bench_user_id AS tech, j.started, j.finished, j.due,
                   wo.accepted_at, wo.handed_over_at,
                   COALESCE(wo.finisher_user_id, wo.bench_user_id) AS finisher,
                   wo.state, COALESCE(wo.duration_expected, 0) AS expected,
                   so.patient, so.partner_id, so.priority
              FROM j
              JOIN mrp_workorder wo ON wo.id = j.id
              JOIN mrp_production mo ON mo.id = wo.production_id
         LEFT JOIN sale_order so ON so.id = mo.sale_id
        CROSS JOIN LATERAL (
                   SELECT wo.bench_user_id AS uid WHERE wo.bench_user_id IS NOT NULL
                   UNION ALL
                   SELECT wo.finisher_user_id WHERE wo.finisher_user_id IS NOT NULL
                      AND wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                   ) p
             WHERE j.started IS NOT NULL AND j.started < %(end)s AND j.started >= %(since)s
               AND (j.finished >= %(start)s
                    OR (j.finished IS NULL AND wo.state NOT IN ('done', 'cancel')))
        """, jobs=self._job_times(where), start=start, end=end, since=since))

    @api.model
    def _finished(self, windows, uids=None):
        """(person credited, finish) of every job finished inside one of `windows`,
        by the same times the day draws - for the usual day and the fortnight strip."""
        lo, hi = min(a for a, _b in windows), max(b for _a, b in windows)
        where = SQL("""(wo.bench_user_id IS NOT NULL OR wo.finisher_user_id IS NOT NULL)
                       AND (wo.date_finished >= %(lo)s OR wo.handed_over_at >= %(lo)s)
                       AND (wo.date_finished < %(hi)s OR wo.handed_over_at < %(hi)s)""", lo=lo, hi=hi)
        inside = SQL(" OR ").join(SQL("(j.finished >= %s AND j.finished < %s)", a, b) for a, b in windows)
        who = SQL("TRUE") if uids is None else SQL("COALESCE(wo.finisher_user_id, wo.bench_user_id) IN %s", tuple(uids) or (0,))
        return self.env.execute_query(SQL("""
            WITH j AS (%(jobs)s)
            SELECT COALESCE(wo.finisher_user_id, wo.bench_user_id), j.finished
              FROM j JOIN mrp_workorder wo ON wo.id = j.id
             WHERE (%(inside)s) AND COALESCE(wo.finisher_user_id, wo.bench_user_id) IS NOT NULL AND %(who)s
        """, jobs=self._job_times(where), inside=inside, who=who))

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
        """The Time Tracking lines of the day, each credited to the people named on its
        work order (`uid`) - not to whoever's name is on the line (`by`), who on this
        floor is mostly the lead scanning for the room. A line on a work order nobody
        is named on stays with the person who ran it."""
        return self.env.execute_query_dict(SQL("""
            SELECT p.workorder_id, COALESCE(c.uid, p.user_id) AS uid, p.user_id AS by, p.workcenter_id, p.date_start,
                   COALESCE(p.date_end, %(now)s) AS date_end, p.date_end IS NULL AS open,
                   COALESCE(t.loss_type, 'productive') AS loss_type
              FROM mrp_workcenter_productivity p
         LEFT JOIN mrp_workorder wo ON wo.id = p.workorder_id
         LEFT JOIN LATERAL (
                   SELECT wo.bench_user_id AS uid WHERE wo.bench_user_id IS NOT NULL
                   UNION ALL
                   SELECT wo.finisher_user_id WHERE wo.finisher_user_id IS NOT NULL
                      AND wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                   ) c ON TRUE
         LEFT JOIN mrp_workcenter_productivity_loss l ON l.id = p.loss_id
         LEFT JOIN mrp_workcenter_productivity_loss_type t ON t.id = l.loss_id
             WHERE p.date_start < %(end)s AND COALESCE(p.date_end, %(now)s) > %(start)s
               AND p.user_id IS NOT NULL AND p.user_id NOT IN %(skip)s
        """, start=start, end=end, now=now, skip=self._skip()))

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
            SELECT e.user_id, a.check_in, COALESCE(a.check_out, LEAST(%(now)s, %(end)s)), a.check_out IS NULL AS open
              FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id
             WHERE e.user_id IS NOT NULL AND a.check_in < %(end)s
               AND COALESCE(a.check_out, %(now)s) > %(start)s
               -- a check-in from an earlier day never checked out is a forgotten
               -- check-out, not somebody at work since midnight
               AND (a.check_out IS NOT NULL OR a.check_in >= %(start)s)
        """, start=start, end=end, now=now))

    @api.model
    def _waiting(self, start, end, now):
        """Arrived-but-not-picked-up intervals per station, each with the job it was.
        A step arrives when the step before it is handed on (the first step when the
        order is made)."""
        since = start - timedelta(days=WAIT_LOOKBACK_DAYS)
        return self.env.execute_query_dict(SQL("""
            WITH touched AS (
                SELECT DISTINCT production_id FROM mrp_workorder
                 WHERE create_date >= %(since)s OR handed_over_at >= %(since)s OR accepted_at >= %(since)s
            ), steps AS (
                SELECT w.id, w.name, w.workcenter_id, w.production_id, w.state, w.accepted_at, w.handed_over_at, w.create_date,
                       w.bench_user_id, w.bench_assigned_at,
                       -- an Upper & Lower case is two chains in one order (mrp_production
                       -- ARCH_BAND): the lower arch's first step follows no upper step
                       LAG(w.handed_over_at) OVER (PARTITION BY w.production_id, COALESCE(w.arch, '')
                                                   ORDER BY w.sequence, w.id) AS prev_done,
                       LAG(w.id) OVER (PARTITION BY w.production_id, COALESCE(w.arch, '')
                                       ORDER BY w.sequence, w.id) AS prev_id
                  FROM mrp_workorder w WHERE w.production_id IN (SELECT production_id FROM touched)
            )
            SELECT s.id, s.name, s.workcenter_id, mo.name AS mo, so.name AS so, so.patient, so.priority,
                   s.bench_user_id AS given_to, s.bench_assigned_at AS given_at,
                   GREATEST(s.create_date, COALESCE(s.prev_done, s.create_date)) AS arrived,
                   COALESCE(s.accepted_at, s.handed_over_at, %(now)s) AS picked,
                   (s.accepted_at IS NULL AND s.handed_over_at IS NULL) AS open
              FROM steps s
              JOIN mrp_production mo ON mo.id = s.production_id
         LEFT JOIN sale_order so ON so.id = mo.sale_id
             WHERE s.state <> 'cancel' AND (s.prev_id IS NULL OR s.prev_done IS NOT NULL)
               AND NOT (s.state = 'done' AND s.accepted_at IS NULL AND s.handed_over_at IS NULL)
               AND GREATEST(s.create_date, COALESCE(s.prev_done, s.create_date)) >= %(since)s
               AND GREATEST(s.create_date, COALESCE(s.prev_done, s.create_date)) < %(end)s
               AND COALESCE(s.accepted_at, s.handed_over_at, %(now)s) > %(start)s
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
    def _shifts(self, day, uids):
        """Each person's expected hours for this weekday, from their employee's
        working-hours calendar: the work periods and the breaks (lunch), in minutes
        from local midnight. Nothing for a person without an employee or a calendar -
        then the screen has no shift to hold them to and says nothing.

        Odoo 20 reshaped working hours, so this asks the calendar instead of reading
        its table: the calendar is the one the employee's contract version holds on
        `day` (`_get_calendars`), its lines for that date come from
        `_get_attendances_by_date` (a fixed week, dated lines, recurrences), and the
        hours are wall clock in the EMPLOYEE's timezone - calendars no longer carry
        one. There is no "lunch" line any more: the break is the gap between the
        day's work periods (08-12 and 13-17 -> lunch 12-13). A line with a duration
        and no hours ("8 hours", Odoo 20's default week) says how long, not when,
        so it is no shift; neither is a flexible calendar."""
        if not uids or 'hr.employee' not in self.env:
            return {}
        employees = self.env['hr.employee'].sudo().search([('user_id', 'in', list(uids))])
        if not employees:
            return {}
        calendars = employees._get_calendars(day)
        lab = self.env['lab.station']._lab_tz()
        noon = datetime.combine(day, time(12))
        offsets = {}

        def offset(tzname):
            """Minutes to add to the employee's wall clock to read it on the lab's."""
            if tzname not in offsets:
                try:
                    cal = pytz.timezone(tzname) if tzname else lab
                except pytz.UnknownTimeZoneError:
                    cal = lab
                offsets[tzname] = int((lab.utcoffset(noon) - cal.utcoffset(noon)).total_seconds() // 60)
            return offsets[tzname]
        lines_of = {}
        work_of = defaultdict(set)
        for employee in employees:
            calendar = calendars.get(employee.id)
            if not calendar or calendar._is_flexible():
                continue
            if calendar.id not in lines_of:
                lines = calendar._get_attendances_by_date(day, day).get(day) or []
                lines_of[calendar.id] = [(l.hour_from, l.hour_to) for l in lines if not l.duration_based]
            shift_by = offset(employee._get_tz(date=day))
            for h_from, h_to in lines_of[calendar.id]:
                a, b = int(round(h_from * 60)) + shift_by, int(round(h_to * 60)) + shift_by
                a, b = max(0, min(DAY, a)), max(0, min(DAY, b))
                if b > a:
                    # an employee per company repeats the same lines: a set
                    work_of[employee.user_id.id].add((a, b))
        out = {}
        for uid, periods in work_of.items():
            work = []
            for a, b in sorted(periods):
                if work and a <= work[-1][1]:
                    work[-1] = (work[-1][0], max(work[-1][1], b))
                else:
                    work.append((a, b))
            out[uid] = {'work': work, 'breaks': [(x[1], y[0]) for x, y in zip(work, work[1:])],
                        'a': work[0][0], 'b': work[-1][1]}
        return out

    @api.model
    def _clock_label(self, dt, start, end=None):
        """A stored time on the lab's clock: 14:05 on the day, '13 Oct 17:40' off it."""
        local = self.env['lab.station']._lab_time(dt)
        on_day = dt >= start and (end is None or dt < end)
        return local.strftime('%H:%M') if on_day else local.strftime('%d %b %H:%M')

    @api.model
    def _table_exists(self, name):
        return bool(self.env.execute_query(SQL("SELECT 1 FROM information_schema.tables WHERE table_name = %s", name)))

    @api.model
    def _usual(self, day, uids, slot, tz):
        """Average jobs handed on per slot on the same weekday of the previous weeks."""
        Station = self.env['lab.station']
        windows = [Station._day_window(day - timedelta(weeks=w)) for w in range(1, USUAL_WEEKS + 1)]
        if not uids:
            return {}
        # the same person the day credits the hand-over to: the finisher where one
        # is named, else the technician - never who pressed the button
        usual = defaultdict(lambda: defaultdict(float))
        for uid, finished in self._finished(windows, uids):
            local = Station._lab_time(finished)
            m = local.hour * 60 + local.minute
            usual[uid][(m // slot) * slot] += 1.0 / USUAL_WEEKS
        return usual

    @api.model
    def _strip(self, day, tz):
        """Jobs handed on and people per day, for the fortnight ending on `day`."""
        Station = self.env['lab.station']
        first = day - timedelta(days=STRIP_DAYS - 1)
        a, _b = Station._day_window(first)
        _a, b = Station._day_window(day)
        jobs, people = defaultdict(int), defaultdict(set)
        for uid, finished in self._finished([(a, b)]):
            d = Station._lab_time(finished).date()
            jobs[d] += 1
            people[d].add(uid)
        out = []
        for i in range(STRIP_DAYS):
            d = first + timedelta(days=i)
            out.append({'date': fields.Date.to_string(d), 'dow': d.strftime('%a'), 'day': d.day,
                        'jobs': jobs.get(d, 0), 'people': len(people.get(d, ()))})
        return out

    @staticmethod
    def _periods(flags, lo, hi, weight=None, least=IDLE_PERIOD_MIN):
        """The runs of set minutes in flags[lo:hi] at least `least` long, as
        {a, b, wait} - `wait` counting the run's minutes that `weight` also flags."""
        out, m = [], lo
        while m < hi:
            if not flags[m]:
                m += 1
                continue
            a = m
            while m < hi and flags[m]:
                m += 1
            if m - a >= least:
                out.append({'a': a, 'b': m, 'wait': sum(weight[a:m]) if weight is not None else 0})
        return out

    # ------------------------------------------------------------------ the day
    @api.model
    def get_day(self, day=None, slot=60, station_ids=None, rostered=False, people=None):
        """Everything the screen draws for one day. Minutes count from local midnight.

        `people`: 'roster' (the default) draws the technicians named on a work
        centre - the ones the floor is made of - and keeps anyone else with a job
        aside (`hidden`, for the insight that names them); 'everyone' draws every
        person with a job, a timer or a check-in. A rostered technician with nothing
        on the day is not a lane but a name under the chart (`absent`). (client,
        2026-10-14: "only show the users who are assigned in any work centres")"""
        self._check_day_access()
        # everything the SQL below reads - jobs, timers, the MO's order and its
        # patient - or a write still in the cache is invisible to it
        self.env.flush_all()
        Station = self.env['lab.station']
        tz = Station._lab_tz()
        day = fields.Date.to_date(day) if day else Station._lab_today()
        slot = int(slot) if int(slot or 0) in SLOTS else 60
        station_ids = [int(s) for s in (station_ids or [])]
        people = people if people in ('roster', 'everyone') else 'roster'
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
            'earned': 0.0, 'handed': 0, 'accepted': 0, 'given': 0, 'carried': 0, 'switches': 0, 'backwards': 0})
        wanted = set(station_ids)

        def keep(wc):
            return not wanted or wc in wanted

        # the people: the technicians named on a work centre are the floor (with a
        # station filter, that station's list)
        roster_all = self._rostered(None)
        roster = self._rostered(station_ids) if station_ids else roster_all
        # who did something of their own on the day at ANY station - a scan, a clock;
        # with the check-ins, `seen`: never "not in", whatever the filter shows
        worked = set()

        bench = self._bench(start, end, since)
        timers = self._timers(start, end, now)
        # the floor counts each JOB once, however many people are named on it
        handed_jobs, accepted_jobs, handed_station, accepted_station = {}, {}, {}, {}

        def stale(t):
            """A clock started on an earlier day: left running overnight, or still
            running - either way not this day's work."""
            return t['date_start'] < start
        timed = defaultdict(list)
        clocked_by = set()
        for t in timers:
            if not stale(t):
                worked.add(t['uid'])
                worked.add(t['by'])       # the lead who ran the clock was at the floor too
            clocked_by.add(t['by'])
            if keep(t['workcenter_id']):
                timed[(t['workorder_id'], t['uid'])].append(t)
        productions = set()
        partners = set()
        for job in bench:
            if job['uid'] and (start <= job['started'] < end
                               or (job['finished'] and start <= job['finished'] < end)):
                worked.add(job['uid'])
            if not keep(job['workcenter_id']) or not job['uid']:
                continue
            productions.add(job['production_id'])
            if job['partner_id']:
                partners.add(job['partner_id'])
            u = users[job['uid']]
            u['stations'].add(job['workcenter_id'])
            # the work order's own Start -> End; a running job runs to now
            carried = job['started'] < start
            a = self._minute(max(job['started'], start), start)
            b = self._minute(min(job['finished'] or cap, end), start)
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
                   'a': a, 'b': b, 'kind': kind, 'open': not job['finished'],
                   'expected': round(job['expected'] or 0.0, 1),
                   'started': self._clock_label(job['started'], start, end),
                   'finished': self._clock_label(job['finished'], start, end) if job['finished'] else None,
                   # a running job's End Date on its form: when it is due
                   'due': self._minute(min(job['due'], end), start) if job['due'] and not job['finished'] and job['due'] >= start else None,
                   'due_label': self._clock_label(job['due'], start, end) if job['due'] and not job['finished'] else None,
                   'accepted': fields.Datetime.to_string(job['accepted_at']) if job['accepted_at'] else None,
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
                u['segments'].append(dict(seg, a=ta, b=tb, by=t['by'],
                                          kind='timer' if t['loss_type'] in ('productive', 'performance') else 'blocked'))
            # each moment is one event on one row: the start on the technician's
            # (the finisher's, only when there is no technician), the finish on
            # the finisher's - so a job two people share is never counted twice
            if start <= job['started'] < end and job['uid'] == (job['tech'] or job['finisher']):
                m = self._minute(job['started'], start)
                u['accepted'] += 1
                accepted_jobs[job['id']] = m
                accepted_station[job['id']] = job['workcenter_id']
                u['events'].append({'kind': 'accept', 'm': m, 'wo': job['id'],
                                    'station_id': job['workcenter_id'], 'label': job['mo']})
            if job['finished'] and start <= job['finished'] < end:
                m = self._minute(job['finished'], start)
                u['handed'] += 1
                u['earned'] += job['expected'] or 0.0
                handed_jobs[job['id']] = m
                handed_station[job['id']] = job['workcenter_id']
                if job['accepted_at'] and job['handed_over_at'] and job['accepted_at'] > job['handed_over_at']:
                    u['backwards'] += 1     # picked up after it was handed on: a stamp out of order
                u['events'].append({'kind': 'handed', 'm': m, 'wo': job['id'],
                                    'station_id': job['workcenter_id'], 'label': job['mo']})
        # timers on jobs that are not on anybody's bench (the plain Odoo start/stop) -
        # named all the same, so the bar says which case it was
        named = self._job_names([wo_id for (wo_id, _uid) in timed])
        for (wo_id, uid), rows_ in timed.items():
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
                    'started': None, 'finished': None, 'due': None, 'due_label': None, 'accepted': None, 'handed': None}
            for t in rows_:
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
                u['segments'].append(dict(base, station_id=t['workcenter_id'], a=ta, b=tb, by=t['by'],
                                          kind='timer' if good else 'blocked', open=False))
        for wo_id, uid, at, wc in self._given(start, end):
            if keep(wc):
                users[uid]['given'] += 1
                users[uid]['events'].append({'kind': 'given', 'm': self._minute(at, start), 'wo': wo_id, 'station_id': wc, 'label': ''})
        has_attendance = set()
        seen = set(worked)
        for uid, a_in, a_out, open_ in self._attendance(start, end, now):
            seen.add(uid)
            # with a station filter, a check-in counts for somebody with work there, or
            # on its list with no work anywhere (in, and idle) - not for somebody who
            # worked elsewhere: that would turn their day into this station's idle
            if wanted and uid not in users and (uid not in roster or uid in worked):
                continue
            u = users[uid]
            a, b = self._minute(max(a_in, start), start), self._minute(min(a_out, end), start)
            self._fill(u['present'], a, b)
            u['attendance'].append({'a': a, 'b': b, 'open': bool(open_)})
            has_attendance.add(uid)

        # the stations' own day counts every job, whoever's list its technician is on:
        # bench minutes per person, a job's timer runs only where no bench span holds it
        bench_by = defaultdict(int)
        for u in users.values():
            benched = {s_['wo'] for s_ in u['segments'] if s_['kind'] == 'bench'}
            for s_ in u['segments']:
                if s_['kind'] == 'bench' or (s_['kind'] == 'timer' and s_['wo'] not in benched):
                    bench_by[s_['station_id']] += s_['b'] - s_['a']
        # off the roster: kept aside, and named only for floor work - a check-in alone
        # (office staff, a field executive) is not somebody missing from a station list
        hidden_ids = []
        if people == 'roster':
            off_roster = [uid for uid in list(users) if uid not in roster_all]
            hidden_ids = [uid for uid in off_roster
                          if users[uid]['segments'] or any(e['kind'] != 'given' for e in users[uid]['events'])]
            for uid in off_roster:
                del users[uid]
        for uid in roster:
            users[uid]            # touch: the rostered technician with nothing today is still a name

        # work waiting per station, minute by minute - only at stations that use the
        # board: where nobody ever scans, "not picked up" means "not scanned", not a queue
        scanning = {r[0] for r in self.env.execute_query(SQL(
            "SELECT DISTINCT workcenter_id FROM mrp_workorder WHERE accepted_at >= %s AND accepted_at < %s",
            start - timedelta(days=14), end))}
        waiting = defaultdict(lambda: [0] * DAY)         # every job waiting, for the floor
        # whose minute an idle one is: a job given out to a person and not yet picked
        # up is theirs; a queue nobody was given is the hand-out's (the lead's). On a
        # floor that does not hand out, nothing is given and every queue is open.
        open_wait = defaultdict(lambda: bytearray(DAY))  # waiting and not (yet) given to anybody, per station
        own_wait = defaultdict(lambda: bytearray(DAY))   # given to this person and not yet picked up
        queue = defaultdict(list)
        arrivals = [0] * DAY
        arrived_by = defaultdict(int)
        for w in self._waiting(start, end, now):
            wc = w['workcenter_id']
            if not keep(wc) or wc not in scanning:
                continue
            a, b = self._minute(max(w['arrived'], start), start), self._minute(min(w['picked'], cap if is_today else end), start)
            counts = waiting[wc]
            for m in range(a, b):
                counts[m] += 1
            given_to, given_m = None, None
            if w['given_to'] and w['given_at'] and w['given_at'] < w['picked']:
                given_to, given_m = w['given_to'], self._minute(max(w['given_at'], start), start)
                self._fill(open_wait[wc], a, min(b, given_m))
                self._fill(own_wait[given_to], max(a, given_m), b)
            else:
                self._fill(open_wait[wc], a, b)
            if w['arrived'] >= start:
                arrivals[self._minute(w['arrived'], start)] += 1
                arrived_by[wc] += 1
            if b > a or (w['open'] and is_today):
                queue[wc].append({'wo': w['id'], 'name': w['name'] or '', 'mo': w['mo'] or '', 'so': w['so'] or '',
                                  'patient': (w['patient'] or '').strip(), 'priority': w['priority'] or 'normal',
                                  'a': a, 'b': b, 'open': bool(w['open']), 'given_to': given_to, 'given_at': given_m,
                                  'arrived_today': w['arrived'] >= start,
                                  'arrived_label': self._clock_label(w['arrived'], start),
                                  'wait_min': max(0, int((w['picked'] - w['arrived']).total_seconds() // 60))})

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

        referenced = set(users) | {j['given_to'] for jobs in queue.values() for j in jobs if j['given_to']} | clocked_by
        names = {u.id: u for u in self.env['res.users'].sudo().browse(list(referenced)).exists()}
        stations = {w.id: w for w in self.env['mrp.workcenter'].sudo().browse(
            list({s for u in users.values() for s in u['stations']} | set(waiting) | {s for ss in roster.values() for s in ss})).exists()}
        usual = self._usual(day, list(names), slot, tz)
        shifts = self._shifts(day, list(names))
        rows, floor = [], [{'m': s, 'busy_people': 0, 'handed': 0, 'accepted': 0, 'busy_min': 0, 'waiting': 0, 'arrived': 0,
                            'idle_wait': 0} for s in slots]
        idle_of = {}
        for uid, u in users.items():
            user = names.get(uid)
            if not user:
                continue
            events = sorted(u['events'], key=lambda e: e['m'])
            busy, held, present, active = u['busy'], u['held'], u['present'], u['active']
            shift = shifts.get(uid)
            # the person's own scans: a job GIVEN to them is the lead's stamp and says
            # nothing about whether they came in
            scans = [e['m'] for e in events if e['kind'] != 'given']
            span = [m for m in (active.find(1),) if m >= 0] + scans
            if uid not in has_attendance and span:
                # no attendance: present from the first scan to the last (a carried-in
                # case alone does not make anybody present) - and today on to now while
                # the shift runs: the last scan ended a job, not the day, and the floor
                # must see who stands free. A day gone by keeps first to last scan.
                # (review, 2026-10-14: 0 of 49 technicians check in on this floor)
                first = min(span)
                last = max([active.rfind(1) + 1] + [m + 1 for m in scans])
                if is_today and shift and first < now_min < shift['b']:
                    last = max(last, now_min)
                self._fill(present, first, max(first + 1, last))
            # A CLOCK NOBODY STOPPED IS NOT WORK. An open timer has no end, so it runs
            # to `now`: a scan at 00:32 that was never closed reads as fourteen hours
            # "on a job", which is how two technicians both showed 14h 13m at 100%
            # utilisation on a day nobody worked fourteen hours. `stale()` only catches
            # a clock started on an EARLIER day; one started at half past midnight today
            # walks straight past it.
            #
            # Nobody is on a job while they are not at work, so the time counts only
            # inside attendance - and what falls outside becomes `held`, because the job
            # IS still sitting at that bench, which is worth seeing. Utilisation can no
            # longer exceed 100%, and a forgotten timer stops hiding the idle it should
            # have exposed. Only for people whose attendance is on file: for everyone
            # else presence is derived from their own activity just above, so masking
            # by it would say nothing. (client, 2026-09-30)
            if uid in has_attendance:
                for m in range(DAY):
                    if busy[m] and not present[m]:
                        busy[m] = 0
                        held[m] = 1
            my_stations = roster_all.get(uid, set()) | u['stations']
            mine = own_wait.get(uid)
            cover = [open_wait[wc] for wc in my_stations if wc in open_wait] + ([mine] if mine else [])
            wait_any = bytearray(DAY)
            for arr in cover:
                for m in range(lo, hi):
                    if arr[m]:
                        wait_any[m] = 1
            # the expected hours and the lunch between them: a technician at lunch is
            # not idle, however long the queue - so the break minutes leave `idle`
            brk = bytearray(DAY)
            if shift:
                for a, b in shift['breaks']:
                    self._fill(brk, a, b)
            idle = bytearray(1 if present[m] and not busy[m] and not u['blocked'][m] and not brk[m] else 0 for m in range(DAY))
            idle_of[uid] = idle
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
                iw = sum(1 for m in range(s, e_) if idle[m] and wait_any[m])
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
            periods = self._periods(idle, lo, hi, wait_any)
            idle_total = sum(idle)
            idle_own = sum(1 for m in range(lo, hi) if idle[m] and mine[m]) if mine else 0
            # the stamps' honesty: a run of hand-overs inside three minutes at a station
            # that confirms every scan is a tray scanned at the end, not four jobs done
            # in three minutes - and the idle before it is hidden. Said, not scored.
            flags = []
            by_station = defaultdict(list)
            for e in events:
                if e['kind'] == 'handed':
                    by_station[e['station_id']].append(e['m'])
            for wc, ms in by_station.items():
                st = stations.get(wc)
                if st is not None and not st.scan_confirm:
                    continue
                ms.sort()
                i = 0
                while i < len(ms):
                    j = i
                    while j + 1 < len(ms) and ms[j + 1] - ms[i] <= BURST_MIN:
                        j += 1
                    if j - i + 1 >= BURST_N:
                        flags.append({'kind': 'burst', 'm': ms[i], 'n': j - i + 1, 'station_id': wc})
                        i = j + 1
                    else:
                        i += 1
            if u['backwards']:
                flags.append({'kind': 'backwards', 'n': u['backwards']})
            gap = max(periods, key=lambda g: g['b'] - g['a'], default=None)
            first_act = min([e['m'] for e in events] + [m for m in (busy.find(1),) if m >= 0], default=None)
            last_act = max([e['m'] for e in events] + ([busy.rfind(1) + 1] if busy.find(1) >= 0 else []), default=None)
            first_present = present.find(1)
            last_present = present.rfind(1) + 1 if first_present >= 0 else None
            # the day against the shift: a start after the calendar's, an end before it -
            # from the check-in only: a first scan is when the first job was picked up,
            # not when the person came in
            late = early = 0
            if shift and first_present >= 0 and uid in has_attendance:
                late = max(0, first_present - shift['a'])
                if not is_today or now_min >= shift['b']:
                    early = max(0, shift['b'] - last_present)
            # where the person stands right now, for the live panel
            now_state, idle_since = None, None
            if is_today:
                m = max(0, min(DAY - 1, now_min - 1))
                if busy[m]:
                    now_state = 'busy'
                elif brk[m] and present[m]:
                    now_state = 'break'
                elif idle[m]:
                    now_state = 'idle'
                    idle_since = m
                    while idle_since > 0 and idle[idle_since - 1]:
                        idle_since -= 1
                elif present[m]:
                    now_state = 'blocked'
                else:
                    now_state = 'off'
            rows.append({
                'id': uid, 'name': user.name, 'initials': ''.join(p[:1] for p in (user.name or '?').split()[:2]).upper(),
                'cells': cells, 'segments': sorted(u['segments'], key=lambda s: (s['a'], s['b'])), 'events': events,
                'attendance': u['attendance'], 'has_attendance': uid in has_attendance,
                'stations': sorted(my_stations), 'rostered': uid in roster_all,
                'busy': busy_total, 'present': present_total, 'idle_wait': idle_wait_total,
                'idle': idle_total, 'idle_free': max(0, idle_total - idle_wait_total), 'idle_periods': periods,
                'idle_own': idle_own, 'idle_nobody': max(0, idle_wait_total - idle_own), 'flags': flags,
                'break_min': sum(1 for m in range(DAY) if brk[m] and present[m] and not busy[m] and not u['blocked'][m]),
                'shift': {'a': shift['a'], 'b': shift['b'], 'work': [list(p) for p in shift['work']],
                          'breaks': [list(p) for p in shift['breaks']]} if shift else None,
                'late': late, 'early': early,
                'now_state': now_state, 'idle_since': idle_since,
                'utilisation': round(busy_total / present_total * 100) if present_total else None,
                'handed': u['handed'], 'accepted': u['accepted'], 'given': u['given'], 'carried': u['carried'],
                'earned': round(u['earned']), 'efficiency': round(u['earned'] / busy_total * 100) if busy_total and u['earned'] else None,
                'switches': u['switches'], 'parallel': max(u['stack'][lo:hi]) if hi > lo else 0,
                'longest_gap': {'m': gap['a'], 'len': gap['b'] - gap['a']} if gap and gap['b'] - gap['a'] >= 30 else None,
                'first': first_act, 'last': last_act,
                'first_present': first_present if first_present >= 0 else None, 'last_present': last_present,
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
        # a rostered technician with nothing on the day - no job, no timer, no check-in -
        # is a name under the chart, not an empty lane
        def nothing(r):
            return not r['present'] and not r['segments'] and all(e['kind'] == 'given' for e in r['events'])
        # ...and somebody who worked at another station than the filter's is not "not in"
        absent = [r for r in rows if nothing(r) and r['id'] not in seen]
        rows = [r for r in rows if not nothing(r)]
        hidden = [{'id': u.id, 'name': u.name} for u in self.env['res.users'].sudo().browse(hidden_ids).exists()]
        now_panel = self._now_panel(rows, queue, roster_all, stations, now_min) if is_today else None
        end_m = now_min if is_today else hi
        picked_by = defaultdict(int)
        for wc in accepted_station.values():
            picked_by[wc] += 1
        handed_by = defaultdict(int)
        for wo_id, wc in handed_station.items():
            handed_by[wc] += 1
        # the day per station: what came, what left, what the queue did, who was there
        station_day, uncovered_of = [], {}
        for wc in set(waiting) | set(bench_by) | set(picked_by) | set(handed_by):
            w = stations.get(wc)
            if w is None:
                continue
            counts = waiting.get(wc)
            on_list = [uid for uid, ss in roster_all.items() if wc in ss]
            cover = bytearray(DAY)
            for uid in on_list:
                if uid in idle_of:
                    cover = bytearray(x | y for x, y in zip(cover, idle_of[uid]))
            uncovered = [m for m in range(lo, min(hi, end_m)) if counts and counts[m] and not cover[m]]
            uncovered_of[wc] = uncovered
            jobs = queue.get(wc, [])
            station_day.append({
                'station_id': wc, 'name': w.name, 'arrived': arrived_by.get(wc, 0), 'picked': picked_by.get(wc, 0),
                'handed': handed_by.get(wc, 0), 'queue_start': counts[lo] if counts else 0,
                'queue_end': counts[max(lo, min(DAY, end_m) - 1)] if counts else 0,
                'longest_wait': max((j['b'] - j['a'] for j in jobs), default=0),
                'rostered': len(on_list), 'present': sum(1 for uid in on_list if uid in users and users[uid]['present'].find(1) >= 0),
                'bench_min': bench_by.get(wc, 0), 'uncovered': len(uncovered),
            })
        station_day.sort(key=lambda d_: (-(d_['queue_end'] - d_['queue_start']), -d_['uncovered'], d_['name'] or ''))
        # rush cases passed over: a normal job picked up while an urgent or emergency
        # one stood waiting at the same station, not given to somebody else
        passed = []
        for uid, u in users.items():
            segs = {s_['wo']: s_ for s_ in u['segments']}
            for e in u['events']:
                if e['kind'] != 'accept':
                    continue
                seg = segs.get(e['wo']) or {}
                rank = RUSH_RANK.get(seg.get('priority') or 'normal', 0)
                skipped = [q for q in queue.get(e['station_id'], [])
                           if q['wo'] != e['wo'] and q['a'] <= e['m'] < q['b'] and RUSH_RANK.get(q['priority'], 0) > rank
                           # not given to somebody else AT THAT MINUTE
                           and (q['given_to'] is None or q['given_to'] == uid or q['given_at'] > e['m'])]
                if skipped:
                    passed.append({'m': e['m'], 'wo': e['wo'], 'user_id': uid, 'station_id': e['station_id'],
                                   'so': seg.get('so') or seg.get('mo') or '', 'patient': seg.get('patient') or '',
                                   'skipped': [{'wo': q['wo'], 'so': q['so'] or q['mo'], 'patient': q['patient'], 'priority': q['priority'],
                                                'a': q['a'], 'waited': e['m'] - q['a']} for q in sorted(skipped, key=lambda q: q['a'])]})
        passed.sort(key=lambda p_: p_['m'])
        # the hand-out: how long a job waited before somebody was given it, and how
        # many were given out only after their technician had stood idle a while
        lags, late_handouts = [], []
        for wc, jobs in queue.items():
            for j in jobs:
                if j['given_at'] is None or not j['arrived_today']:
                    continue
                lags.append(max(0, j['given_at'] - j['a']))
                idle = idle_of.get(j['given_to'])
                g = j['given_at']
                if idle is not None and g >= LATE_MIN and all(idle[m] for m in range(g - LATE_MIN, g)):
                    late_handouts.append({'wo': j['wo'], 'so': j['so'] or j['mo'], 'user_id': j['given_to'], 'station_id': wc, 'm': g, 'a': j['a']})
        handout = {'n': len(lags), 'median': sorted(lags)[len(lags) // 2] if lags else None, 'late': late_handouts[:20]}
        insights = self._insights(rows, floor, slot, stations, lo, hi, idle_of, roster_all, hidden, now_panel, shifts,
                                  station_day, uncovered_of, passed, handout)
        partners_map = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(list(partners)).exists()}
        total_present = sum(r['present'] for r in rows)
        total_busy = sum(r['busy'] for r in rows)
        total_earned = sum(r['earned'] for r in rows)
        peak = max(floor, key=lambda f: (f['handed'], f['busy_people']), default=None)
        return {
            'day': fields.Date.to_string(day), 'weekday': day.strftime('%A'), 'is_today': is_today, 'now': now_min,
            'slot': slot, 'slots': slots, 'lo': lo, 'hi': hi, 'tz': tz.zone, 'people': people,
            'rows': rows, 'absent': [{'id': r['id'], 'name': r['name'], 'initials': r['initials'], 'stations': r['stations'],
                                      'shift': r['shift']} for r in absent],
            'hidden': hidden, 'floor': floor, 'insights': insights, 'strip': self._strip(day, tz),
            'queue': {wc: sorted(jobs, key=lambda j: j['a']) for wc, jobs in queue.items()},
            'station_day': station_day, 'passed_over': passed[:30], 'handout': handout,
            'stations': [{'id': w.id, 'name': w.name, 'hue': self._hue(w.id)} for w in sorted(stations.values(), key=lambda w: w.name or '')],
            'station_ids': station_ids, 'partners': partners_map,
            'names': {uid: u.name for uid, u in names.items()},
            'kpis': {
                'people': sum(1 for r in rows if r['busy'] or r['handed'] or r['accepted']),
                'rostered': len(roster), 'absent': len(absent), 'handed': len(handed_jobs), 'accepted': len(accepted_jobs),
                'busy_h': round(total_busy / 60.0, 1), 'present_h': round(total_present / 60.0, 1),
                'utilisation': round(total_busy / total_present * 100) if total_present else None,
                'earned_h': round(total_earned / 60.0, 1),
                'efficiency': round(total_earned / total_busy * 100) if total_busy and total_earned else None,
                'idle_wait_h': round(sum(r['idle_wait'] for r in rows) / 60.0, 1),
                'idle_h': round(sum(r['idle'] for r in rows) / 60.0, 1),
                'idle_own_h': round(sum(r['idle_own'] for r in rows) / 60.0, 1),
                'late': sum(1 for r in rows if r['late'] >= LATE_MIN),
                'passed_over': len(passed), 'bursts': sum(1 for r in rows for f in r['flags'] if f['kind'] == 'burst'),
                'carried': sum(r['carried'] for r in rows),
                'peak': {'m': peak['m'], 'handed': peak['handed']} if peak and peak['handed'] else None,
                'arrived': sum(f['arrived'] for f in floor),
            },
        }

    @api.model
    def _now_panel(self, rows, queue, roster, stations, now_min):
        """The floor this minute: who is on a job, who stands free and since when, and
        the jobs waiting at each station with the free technicians named on it. It is
        not drawn - the "Right now" panel was taken off the screen (client,
        2026-10-14: "not needed") - and is worked out only for the insight that says
        who stands free while work waits."""
        free = [r for r in rows if r['now_state'] == 'idle']
        free_ids = {r['id'] for r in free}
        state_of = {r['id']: r['now_state'] for r in rows}
        queues = []
        for wc, jobs in queue.items():
            open_jobs = [j for j in jobs if j['open']]
            if not open_jobs:
                continue
            named = sorted({uid for uid, ss in roster.items() if wc in ss})
            first = max(open_jobs, key=lambda j: j['wait_min'])
            queues.append({
                'station_id': wc, 'name': stations[wc].name if wc in stations else '',
                'jobs': sorted(open_jobs, key=lambda j: (j['priority'] not in ('emergency', 'urgent'), -j['wait_min'])),
                'oldest': first['a'], 'oldest_label': first['arrived_label'], 'oldest_wait': first['wait_min'],
                'free': [uid for uid in named if uid in free_ids],
                'named': len(named),
                'in': sum(1 for uid in named if state_of.get(uid) not in (None, 'off')),
                'on_job': sum(1 for uid in named if state_of.get(uid) == 'busy'),
            })
        queues.sort(key=lambda q: (-len(q['free']), -q['oldest_wait']))
        return {
            'm': now_min,
            'busy': sum(1 for r in rows if r['now_state'] == 'busy'),
            'at_break': sum(1 for r in rows if r['now_state'] == 'break'),
            'free': [{'id': r['id'], 'name': r['name'], 'since': r['idle_since'],
                      'stations': [wc for wc in r['stations'] if any(j['open'] for j in queue.get(wc, []))]}
                     for r in sorted(free, key=lambda r: r['idle_since'] or 0)],
            'waiting': sum(len(q['jobs']) for q in queues),
            'queues': queues,
        }

    @api.model
    def _insights(self, rows, floor, slot, stations, lo, hi, idle_of=None, roster=None, hidden=None,
                  now_panel=None, shifts=None, station_day=None, uncovered_of=None, passed=None, handout=None):
        """A few sentences a lead can act on, strongest first."""
        out = []
        idle_of, roster, hidden, shifts = idle_of or {}, roster or {}, hidden or [], shifts or {}
        station_day, uncovered_of, passed, handout = station_day or [], uncovered_of or {}, passed or [], handout or {}

        def hm(m):
            return '%02d:%02d' % divmod(int(m), 60)

        def dur(m):
            return '%d h %02d min' % divmod(m, 60) if m >= 60 else '%d min' % m

        def names(ids, limit=3):
            by_id = {r['id']: r['name'] for r in rows}
            picked = [by_id[i] for i in ids if i in by_id][:limit]
            more = len(ids) - len(picked)
            return ', '.join(picked) + (_(" and %d more", more) if more > 0 else '')
        # the floor this minute, first: it is the only line about something still happening
        if now_panel and now_panel['free'] and now_panel['waiting']:
            handable = [q for q in now_panel['queues'] if q['free']]
            if handable:
                q = handable[0]
                out.append({'kind': 'warn', 'user_id': q['free'][0], 'text': _(
                    "Right now %(who)s stand(s) free while %(n)d job(s) wait at %(station)s - the oldest since %(when)s.",
                    who=names(q['free']), n=len(q['jobs']), station=q['name'], when=q['oldest_label'])})
        # a job already given to somebody who stood idle is that person's minute;
        # a queue nobody was given is the hand-out's. Say which.
        own = max(rows, key=lambda r: r['idle_own'], default=None)
        if own and own['idle_own'] >= LATE_MIN:
            out.append({'kind': 'warn', 'user_id': own['id'], 'text': _(
                "%(name)s stood idle %(h)s with a job already given to them.", name=own['name'], h=dur(own['idle_own']))})
        worst = max(rows, key=lambda r: r['idle_nobody'], default=None)
        if worst and worst['idle_nobody'] >= 30:
            spot = max(worst['idle_periods'], key=lambda g: g['wait'], default=None)
            out.append({'kind': 'warn', 'user_id': worst['id'], 'text': _(
                "%(name)s stood idle %(h)s while work waited at their stations and nobody was given it (the longest from %(when)s).",
                name=worst['name'], h=dur(worst['idle_nobody']), when=hm(spot['a']) if spot else '')})
        if handout.get('late'):
            late_h = handout['late']
            out.append({'kind': 'warn', 'user_id': late_h[0]['user_id'], 'text': _(
                "%(n)d job(s) were handed out only after their technician had stood idle %(m)d min or more - the hand-out, not the bench.",
                n=len(late_h), m=LATE_MIN)})
        # work that waited with every technician on the station's list busy: the
        # queue needed hands the station does not have - and who was free elsewhere
        for d_ in sorted(station_day, key=lambda d_: -d_['uncovered']):
            uncovered = uncovered_of.get(d_['station_id'], [])
            if len(uncovered) < 60:
                break
            on_list = {uid for uid, ss in roster.items() if d_['station_id'] in ss}
            others = sorted((uid for uid in idle_of if uid not in on_list and sum(idle_of[uid][m] for m in uncovered) >= 30),
                            key=lambda uid: -sum(idle_of[uid][m] for m in uncovered))
            if not d_['rostered']:
                text = _("Work waited %(h)s at %(station)s, which has nobody on its technician list",
                         h=dur(len(uncovered)), station=d_['name'])
                text += _(" - %(who)s had time then.", who=names(others)) if others else '.'
            elif not d_['present']:
                text = _("Work waited %(h)s at %(station)s and nobody on its list was in",
                         h=dur(len(uncovered)), station=d_['name'])
                text += _(" - %(who)s had time then but are not on its list.", who=names(others)) if others else '.'
            else:
                text = _("Work waited %(h)s at %(station)s with everyone on its list busy",
                         h=dur(len(uncovered)), station=d_['name'])
                if others:
                    text += _(" - %(who)s had time then but are not on its list.", who=names(others))
                else:
                    text += _(" - the station needs another pair of hands then.")
            out.append({'kind': 'warn', 'user_id': others[0] if others else None, 'text': text})
            break
        if passed:
            p0 = passed[0]
            sk = p0['skipped'][0]
            out.append({'kind': 'warn', 'user_id': p0['user_id'], 'text': _(
                "%(n)d pick-up(s) passed over an urgent or emergency case waiting at the same station - %(so)s waited %(h)s at %(station)s.",
                n=len(passed), so=sk['so'] or _('a rush case'), h=dur(sk['waited']),
                station=stations[p0['station_id']].name if p0['station_id'] in stations else '?')})
        behind = max(station_day, key=lambda d_: d_['queue_end'] - d_['queue_start'], default=None)
        if behind and behind['queue_end'] - behind['queue_start'] >= 3:
            out.append({'kind': 'warn', 'text': _(
                "%(station)s fell behind: %(a)d arrived, %(h)d handed on, queue %(q0)d → %(q1)d, %(p)d of %(r)d on its list in.",
                station=behind['name'], a=behind['arrived'], h=behind['handed'], q0=behind['queue_start'], q1=behind['queue_end'],
                p=behind['present'], r=behind['rostered'])})
        late = sorted((r for r in rows if r['late'] >= LATE_MIN), key=lambda r: -r['late'])
        if late:
            out.append({'kind': 'info', 'user_id': late[0]['id'], 'text': _(
                "%(who)s started %(late)s after the %(at)s on the roster.",
                who=names([r['id'] for r in late]), late=dur(late[0]['late']), at=hm(late[0]['shift']['a']))})
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
            out.append({'kind': 'warn', 'text': _("%(n)d job(s) were started on an earlier day - held on the chart, not counted as today's work.", n=sum(r['carried'] for r in carried))})
        for r in rows:
            burst = next((f for f in r['flags'] if f['kind'] == 'burst'), None)
            if burst:
                out.append({'kind': 'muted', 'user_id': r['id'], 'text': _(
                    "%(name)s handed on %(n)d jobs within %(w)d minutes at %(when)s - scanned as a batch? The idle before it is hidden.",
                    name=r['name'], n=burst['n'], w=BURST_MIN, when=hm(burst['m']))})
                break
        if hidden:
            # kept in sight: it is the only way to the people the roster leaves out
            out = out[:9]
            out.append({'kind': 'muted', 'people': 'everyone', 'text': _(
                "%(n)d other(s) worked but are on no station's technician list: %(who)s.",
                n=len(hidden), who=', '.join(h['name'] for h in hidden[:3]) + (_(" and more") if len(hidden) > 3 else ''))})
        return out[:10]

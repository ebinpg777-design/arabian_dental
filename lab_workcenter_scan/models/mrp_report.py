# -*- coding: utf-8 -*-
"""What the floor did, by week and by person.

Two questions a lab owner asks about a bench and cannot currently answer: how much did
each person get through last week, and are they getting quicker. Both are answered from
stamps the station board writes as people work — accepted, handed on, and the timer
between the two — so nothing has to be filled in afterwards.

Two halves, because the lab has two kinds of production data and only one of them
depends on anyone scanning:

  WHAT WE DID   weeks, people, stations - read from the board's own stamps (accepted,
                handed on, the timer between). Zero until the floor scans.
  RIGHT NOW     the backlog: where the open work is standing, how many hours of it,
                how old it is. Read from the routing and the order dates, which exist
                whether or not anybody has ever touched the board.

That split is the whole point. On this database the floor has never used the board -
111,371 work orders, none handed over - so every figure in the first half is zero and
the screen used to be blank. The second half is never blank: 2,558 cases are live, 1,202
hours of them are queued at one station, and 927 have been in the lab over ninety days.
A report that shows nothing while that is true is not honest, it is broken.
(measured 2026-08-28)
"""
from datetime import timedelta

from odoo import _, api, fields, models

WEEKS = 8
TOP_PEOPLE = 12
TOP_OLDEST = 12
TOP_LATE = 12
# The lab's own standard for an appliance is read from what it actually delivered
# in the last twelve weeks - enough cases to trust a median, recent enough to be
# how the lab works now. A case is LATE when it has been here half again as long
# as usual and at least a day over, so a 2.9-day case is not flagged against a
# 2.0-day usual. (client, 2026-09-10)
USUAL_DAYS = 84
USUAL_MIN_CASES = 5
LATE_FACTOR = 1.5
LATE_MARGIN_DAYS = 1.0
PACE_DAYS = 14
# A pace read from two scans in a fortnight is noise dressed as a number - it
# put "641 days at pace" beside a bench that simply does not scan yet. Fewer
# hand-overs than this in the window and the bench has no pace to show.
PACE_MIN_SCANS = 10
# And a pace below a tenth of the bench's capacity is not a pace either: a
# bench of five does not work ninety minutes a day, the scanner is missing the
# rest. Shown only once the board is catching enough of the work to mean it.
PACE_MIN_SHARE = 0.1
# How many benches and clinics a screen can carry before it stops being read.
TOP_BENCHES = 10
TOP_DOCTORS = 8
# The window the bench radar reads arrivals and departures over.
RADAR_DAYS = 7
# When a case is due out, the way a morning is planned. (client, 2026-09-10)
PROMISE_BUCKETS = [
    ('overdue', 'Should have gone'),
    ('today', 'Due today'),
    ('tomorrow', 'Due tomorrow'),
    ('week', 'This week'),
    ('later', 'Later'),
]

# How the lab talks about how long a case has been here. The last bucket is open-ended
# on purpose: past three months the exact number stops changing the decision.
AGE_BINS = [
    ('fresh', 'Under a week', 0, 7),
    ('week', '1-4 weeks', 8, 30),
    ('month', '1-3 months', 31, 90),
    ('old', 'Over 3 months', 91, None),
]


class LabMrpReport(models.AbstractModel):
    _name = 'lab.mrp.report'
    _description = 'Production — People and Weeks'

    @api.model
    def _week_start(self, day):
        """Monday of that day's week — the week a lab actually talks in."""
        return day - timedelta(days=day.weekday())

    @api.model
    def get_mrp_report(self, weeks=WEEKS):
        # Every `:day` grouping below buckets in the CONTEXT's timezone, which is
        # UTC for a user with none - while the windows are the lab's. One zone
        # for both, or a Monday-morning finish lands in last week's bar.
        self = self.with_context(tz=self.env['lab.station']._lab_tz().zone)
        weeks = max(1, min(int(weeks or WEEKS), 26))
        today = self.env['lab.station']._lab_today()
        first = self._week_start(today) - timedelta(weeks=weeks - 1)
        Perf = self.env['lab.production.performance']
        # The view reads the tables, not the ORM cache: a hand-over written in this
        # same transaction is invisible until it is flushed. (client, 2026-08-28)
        self.env['mrp.workorder'].flush_model()

        # Everything finished since that Monday, in one read. Grouped in the database:
        # this view has a row per work order and there are 111,371 of them.
        done = [('handed_over_at', '>=', self.env['lab.station']._day_window(first)[0])]

        # ONE live read and ONE standard for the whole payload: both halves of
        # this screen want them, and each cost a scan of the floor.
        # (client, 2026-09-10)
        live_now, live_steps = self._live_steps()
        usual = self._usual_days()
        weekly = self._weekly(Perf, done, first, weeks)
        self._weave_redos(weekly, first, weeks)
        people = self._people(Perf, done)
        self._weave_sparks(people, Perf, done, first, weeks)
        stations = self._stations(Perf, done)
        backlog = self._backlog(live_now, live_steps, usual)
        redo = self._redo(first, sum(w['jobs'] for w in weekly))
        pace = self._pace(Perf, done)

        # Has ANY of this ever been recorded? The difference between "a quiet week" and
        # "nobody has ever used the board" is the whole meaning of the screen.
        ever = Perf.search_count([('handed_over_at', '!=', False)])
        return {
            'weeks': weekly,
            'people': people,
            'stations': stations,
            'totals': {
                'jobs': sum(w['jobs'] for w in weekly),
                'people': len(people),
                'hands_on': round(sum(p['minutes'] for p in people) / 60.0, 1),
            },
            'from': fields.Date.to_string(first),
            'never_used': not ever,
            'open_now': backlog['jobs'],
            # The half that never needs the board.
            'backlog': backlog,
            'redo': redo,
            'pace': pace,
            # First-pass yield: the one quality number a lab owner quotes.
            # Floored at zero: a sparse window can hold more redos than
            # finishes (rate over 100%), and "-100% right first time" is not
            # a sentence anyone should read off a dashboard.
            'first_pass': (max(0.0, round(100.0 - redo['rate'], 1))
                           if redo['rate'] is not None else None),
            'activity': self._activity(),
            'flows': self._flows(first),
            # Cases in against cases out, week by week: whether the backlog is
            # growing is the one production number that decides everything else.
            'balance': self._balance(),
            # THE FLOOR AS DECISIONS. What is due out and when, which bench the
            # lab is waiting on, where the old work actually sits, and which
            # doctor has been waiting longest. (client, 2026-09-10)
            **self._floor_now(live_now, live_steps, usual),
        }

    @api.model
    def _floor_now(self, live=None, first=None, usual=None):
        """The four questions the Right-now half exists to answer."""
        if live is None or first is None:
            live, first = self._live_steps()
        usual = self._usual_days() if usual is None else usual
        now = fields.Datetime.now()
        return {
            'promises': self._promises(live, usual, now),
            'radar': self._radar(first),
            'rot': self._rot(live, first, now),
            'doctors': self._waiting_doctors(live, usual, now),
        }

    @api.model
    def _weave_redos(self, weekly, first, weeks):
        """Redos onto the same weekly bars as the finishes: quality and
        output on ONE chart, because a great week with five remakes is not a
        great week."""
        buckets = {(first + timedelta(weeks=i)): 0 for i in range(weeks)}
        for when, count in self.env['lab.mrp.redo']._read_group(
                [('date', '>=', self.env['lab.station']._day_window(first)[0])],
                ['date:day'], ['__count']):
            if when:
                key = self._week_start(fields.Date.to_date(when))
                if key in buckets:
                    buckets[key] += count
        for row in weekly:
            row['redos'] = buckets.get(fields.Date.to_date(row['week']), 0)

    @api.model
    def _weave_sparks(self, people, Perf, domain, first, weeks):
        """Each technician's weeks as a tiny line: 40 finished says nothing
        about whether they are ramping up or burning out; the shape does."""
        per = {}
        for user, when, count in Perf._read_group(
                domain + [('bench_user_id', '!=', False)],
                ['bench_user_id', 'handed_over_at:day'], ['__count']):
            if not when:
                continue
            key = self._week_start(fields.Date.to_date(when))
            per.setdefault(user.id, {}).setdefault(key, 0)
            per[user.id][key] += count
        starts = [first + timedelta(weeks=i) for i in range(weeks)]
        for row in people:
            mine = per.get(row['id'], {})
            row['spark'] = [mine.get(day, 0) for day in starts]

    @api.model
    def _activity(self, days=14):
        """Scanner adoption, day by day: accepts and hand-overs. This is the
        number that moves FIRST as the floor takes up the board - weeks before
        the queues change shape - and the one a manager pushing the habit
        needs to see respond."""
        Workorder = self.env['mrp.workorder']
        today = self.env['lab.station']._lab_today()
        start = today - timedelta(days=days - 1)
        start_dt = self.env['lab.station']._day_window(start)[0]
        # Bucketed in the lab's zone, the one the window above is drawn in.
        Workorder = Workorder.with_context(tz=self.env['lab.station']._lab_tz().zone)

        def per_day(field):
            out = {}
            for when, count in Workorder._read_group(
                    [(field, '>=', start_dt)], [field + ':day'], ['__count']):
                if when:
                    out[fields.Date.to_date(when)] = count
            return out

        accepts, handed = per_day('accepted_at'), per_day('handed_over_at')
        rows = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            rows.append({
                'label': day.strftime('%d'),
                'accepts': accepts.get(day, 0),
                'handed': handed.get(day, 0),
                'weekend': day.weekday() == 6,
            })
        return {
            'rows': rows,
            'peak': max((r['accepts'] + r['handed'] for r in rows), default=0),
            'total': sum(r['accepts'] + r['handed'] for r in rows),
        }

    @api.model
    def _flows(self, first, top=6):
        """Where work actually travels: the busiest station-to-station lanes,
        read from the hand-over trail the scanner leaves. The routing says
        where work SHOULD go; this says where it went."""
        rows = []
        for src, dst, count in self.env['mrp.workorder']._read_group(
                [('handed_from_workcenter_id', '!=', False),
                 ('workcenter_id', '!=', False),
                 ('handed_over_at', '>=', self.env['lab.station']._day_window(first)[0])],
                ['handed_from_workcenter_id', 'workcenter_id'], ['__count'],
                order='__count desc', limit=top):
            rows.append({'from': src.display_name, 'to': dst.display_name,
                         'count': count})
        return rows

    @api.model
    def _weekly(self, Perf, domain, first, weeks):
        """Operations finished per week, whoever did them.

        Grouped by DAY and bucketed here, not by Odoo's `:week`. That granularity
        anchors its buckets on a SUNDAY, so taking "the Monday of" one walked the whole
        row back a week and a job finished on Monday the 24th was reported under the
        17th. One definition of a week, and it is this one. (client, 2026-08-27)
        """
        buckets = {(first + timedelta(weeks=i)): 0 for i in range(weeks)}
        for handed, count in Perf._read_group(
                domain, ['handed_over_at:day'], ['__count']):
            if handed:
                key = self._week_start(fields.Date.to_date(handed))
                if key in buckets:
                    buckets[key] += count
        rows = [{'week': fields.Date.to_string(day),
                 'label': day.strftime('%d %b'),
                 'jobs': count} for day, count in sorted(buckets.items())]
        peak = max((r['jobs'] for r in rows), default=0)
        for row in rows:
            row['pct'] = round(row['jobs'] / peak * 100, 1) if peak else 0.0
        return rows

    @api.model
    def _people(self, Perf, domain):
        """Per person: how much they finished, and how long it took them.

        Ranked on operations finished, not on speed. A bench that is handed the hardest
        appliances is slower by arithmetic, and ranking on minutes would read that as
        the technician's failing.
        """
        rows, by_id = [], {}
        for user, jobs, minutes, bench, waiting in Perf._read_group(
                domain + [('bench_user_id', '!=', False)],
                ['bench_user_id'],
                ['jobs:sum', 'clocked_minutes:sum', 'working_hours:avg',
                 'waiting_hours:avg']):
            by_id[user.id] = {
                'id': user.id,
                'name': user.name,
                'jobs': jobs or 0,
                'minutes': round(minutes or 0.0, 1),
                # The average is what compares two people; the total is what compares
                # two weeks of the same person. Measured over the jobs the minutes
                # belong to - the ones they carried out - never over the finishes.
                'per_job': round((minutes or 0.0) / jobs, 1) if jobs else 0.0,
                'bench_hours': round(bench or 0.0, 1),
                'waiting_hours': round(waiting or 0.0, 1),
                'finished_for_others': 0,
            }
            rows.append(by_id[user.id])
        # Jobs finished for somebody else count under the finisher's name too:
        # a polishing bench does the work it is given, and a table that named
        # only the technician left it with a week of nothing. (client, 2026-09-10)
        for user, jobs in Perf._read_group(
                domain + [('finisher_other_id', '!=', False)],
                ['finisher_other_id'], ['jobs:sum']):
            row = by_id.get(user.id)
            if not row:
                row = {'id': user.id, 'name': user.name, 'jobs': 0, 'minutes': 0.0,
                       'per_job': 0.0, 'bench_hours': 0.0, 'waiting_hours': 0.0,
                       'finished_for_others': 0}
                by_id[user.id] = row
                rows.append(row)
            row['jobs'] += jobs or 0
            row['finished_for_others'] = jobs or 0
        rows.sort(key=lambda r: -r['jobs'])
        rows = rows[:TOP_PEOPLE]
        peak = rows[0]['jobs'] if rows else 0
        for row in rows:
            row['pct'] = round(row['jobs'] / peak * 100, 1) if peak else 0.0
        return rows

    @api.model
    def _stations(self, Perf, domain):
        rows = []
        for station, jobs, minutes in Perf._read_group(
                domain + [('workcenter_id', '!=', False)],
                ['workcenter_id'], ['jobs:sum', 'clocked_minutes:avg']):
            rows.append({'id': station.id, 'name': station.display_name,
                         'jobs': jobs or 0, 'per_job': round(minutes or 0.0, 1)})
        rows.sort(key=lambda r: -r['jobs'])
        peak = rows[0]['jobs'] if rows else 0
        for row in rows:
            row['pct'] = round(row['jobs'] / peak * 100, 1) if peak else 0.0
        return rows

    # ------------------------------------------------------------------ right now
    @api.model
    def _live_steps(self):
        """The open work, one row per case, at the step it is actually waiting at.

        A case routed Wire Bending -> Acrylisation -> Trimming has all three steps
        unfinished from the moment it is created, so counting every unfinished work
        order puts one case in three queues at once. The queue is the FIRST unfinished
        step and only that - the same rule the floor board uses. (client, 2026-08-28)

        One implementation, in report.lab.floor: this used to keep a second copy
        of the same read for the sake of one extra column, so every board paid for
        the scan twice. (client, 2026-09-10)
        """
        Floor = self.env['report.lab.floor']
        live = self.env['mrp.production'].search(Floor._live_domain())
        return live, Floor._first_open_step(live)

    @api.model
    def _backlog(self, live=None, first=None, usual=None):
        """Where the open work is standing, how many hours of it, and how old.

        This is the half of the screen that does not need anybody to scan: the routing
        says which station a case is waiting at and what that step is costed at, and the
        order date says how long it has been here. Both exist on every case.
        """
        if live is None or first is None:
            live, first = self._live_steps()
        now = fields.Datetime.now()
        started = {m.id: (m.date_start or m.create_date) for m in live}

        stations, ages = {}, {key: {'key': key, 'label': label, 'jobs': 0, 'ids': []}
                              for key, label, _lo, _hi in AGE_BINS}
        oldest, total_minutes = [], 0.0
        for production_id, step in first.items():
            when = started.get(production_id)
            days = max(0, (now - when).days) if when else 0
            minutes = step.get('duration_expected') or 0.0
            total_minutes += minutes

            station = step.get('workcenter_id') or (0, _('No station'))
            row = stations.setdefault(station[0], {
                'id': station[0], 'name': station[1], 'jobs': 0, 'minutes': 0.0,
                'oldest': 0, 'ids': []})
            row['jobs'] += 1
            row['minutes'] += minutes
            row['oldest'] = max(row['oldest'], days)
            row['ids'].append(production_id)

            for key, _label, low, high in AGE_BINS:
                if days >= low and (high is None or days <= high):
                    ages[key]['jobs'] += 1
                    ages[key]['ids'].append(production_id)
                    break
            oldest.append((days, production_id, station[1]))

        rows = sorted(stations.values(), key=lambda r: -r['minutes'])
        peak = rows[0]['minutes'] if rows else 0.0
        for row in rows:
            row['hours'] = round(row['minutes'] / 60.0, 1)
            row['pct'] = round(row['minutes'] / peak * 100, 1) if peak else 0.0
            # Only the ids are useful to the client for a drill; the list itself is
            # large, so it is not sent.
            row['ids'] = row['ids'][:0] or []
        self._capacity(rows)
        late = self._late(live, first, started, now, usual)
        age_rows = [ages[key] for key, _l, _lo, _hi in AGE_BINS]
        for row in age_rows:
            row['pct'] = round(row['jobs'] / len(first) * 100, 1) if first else 0.0
            row.pop('ids', None)

        oldest.sort(reverse=True)
        top = oldest[:TOP_OLDEST]
        productions = self.env['mrp.production'].browse([o[1] for o in top])
        by_id = {m.id: m for m in productions}
        oldest_rows = []
        for days, production_id, station in top:
            production = by_id.get(production_id)
            if not production:
                continue
            order = production.sale_id
            oldest_rows.append({
                'id': production_id,
                'name': production.name,
                'days': days,
                'station': station,
                'patient': (order.patient or '') if order else '',
                'clinic': order.partner_id.display_name if order else '',
                'appliance': production.product_id.display_name or '',
                'order': order.name if order else '',
            })
        return {
            'stations': rows,
            'ages': age_rows,
            'oldest': oldest_rows,
            'jobs': len(first),
            'hours': round(total_minutes / 60.0, 1),
            # The queue in days of work, if the whole lab did nothing else. A blunt
            # number on purpose: it is the one a lead can check against the promise
            # they are about to make to a doctor.
            'busiest': rows[0]['name'] if rows else '',
            'busiest_hours': rows[0]['hours'] if rows else 0.0,
            # How long the queue takes to clear if every bench runs flat out:
            # the slowest bench sets it, because work behind it waits.
            **self._days_to_clear(rows),
            'late': late,
        }

    # ------------------------------------------------------------------ capacity
    @api.model
    def _capacity(self, rows):
        """What each bench can do in a day, beside what is waiting at it.

        Hours queued on their own say which bench is deepest, not which is in
        trouble: 457 hours at a bench of five is nine days, at a bench of one
        it is a fortnight. Capacity is people posted times the calendar's hours
        per day; pace is what the bench actually handed on over the last two
        weeks, where the scanner has been used enough to say. Both are shown,
        because the gap between them is the manager's own number.
        (client, 2026-09-10)
        """
        ids = [r['id'] for r in rows if r['id']]
        benches = {w.id: w for w in self.env['mrp.workcenter'].with_context(
            active_test=False).browse(ids)}
        pace = self._bench_pace(ids)
        for row in rows:
            bench = benches.get(row['id'])
            people = len(bench.users | bench.head_user_ids) if bench else 0
            per_day = float((bench and bench.resource_calendar_id.hours_per_day) or 8.0)
            capacity = round(people * per_day, 1)
            cleared = pace.get(row['id'])
            if not self._trust_pace(cleared, capacity):
                cleared = None
            row.update({
                'people': people,
                'hours_per_day': per_day,
                'capacity': capacity,
                'days_at_capacity': round(row['hours'] / capacity, 1) if capacity else None,
                'pace': cleared,
                'days_at_pace': round(row['hours'] / cleared, 1) if cleared else None,
            })

    @api.model
    def _trust_pace(self, cleared, capacity):
        """A measured pace is shown only when the scanner is catching enough of
        the bench's output for the figure to mean something."""
        if not cleared:
            return False
        return not capacity or cleared >= capacity * PACE_MIN_SHARE

    @api.model
    def _bench_pace(self, workcenter_ids, days=PACE_DAYS, min_scans=PACE_MIN_SCANS):
        """{workcenter_id: costed hours handed on per working day, lately}.

        Working days are every day but Sunday, the lab's own week. A bench
        nobody has scanned at in the window is absent rather than zero: no
        scans is no information, not a bench that did nothing - and so is a
        bench with fewer than `min_scans`, whose pace would be a guess.
        """
        if not workcenter_ids:
            return {}
        today = self.env['lab.station']._lab_today()
        first = today - timedelta(days=days - 1)
        start, _end = self.env['lab.station']._day_window(first)
        working = sum(1 for i in range(days)
                      if (first + timedelta(days=i)).weekday() != 6)
        out = {}
        for bench, scans, minutes in self.env['mrp.workorder'].sudo()._read_group(
                [('handed_over_at', '>=', start),
                 ('workcenter_id', 'in', list(workcenter_ids))],
                ['workcenter_id'], ['__count', 'duration_expected:sum']):
            if minutes and working and scans >= min_scans:
                out[bench.id] = round(minutes / 60.0 / working, 1)
        return out

    @api.model
    def _days_to_clear(self, rows):
        """The lab's days to clear: set by its slowest bench, which gates the rest."""
        judged = [r for r in rows if r.get('days_at_capacity') is not None]
        if not judged:
            return {'days_to_clear': None, 'gating': '', 'capacity': 0.0,
                    'unstaffed': [r['name'] for r in rows if r['id'] and not r.get('people')]}
        worst = max(judged, key=lambda r: r['days_at_capacity'])
        return {
            'days_to_clear': worst['days_at_capacity'],
            'gating': worst['name'],
            'capacity': round(sum(r['capacity'] for r in rows), 1),
            # A bench with work at it and nobody posted cannot clear at all.
            'unstaffed': [r['name'] for r in rows if r['id'] and not r.get('people')],
        }

    # ------------------------------------------------------------------ lateness
    @api.model
    def _usual_days(self):
        """{product_id: usual days from order to delivery} - the lab's own standard.

        Read from what was actually delivered in the last twelve weeks, per
        appliance, as a median: a Hawley leaves in two days and a Twin Block
        in three, and "forty days old" is the wrong question for both. The
        delivery is the event that counts, not the order being closed here -
        orders on this database are closed late or never. (client, 2026-09-10)
        """
        since = fields.Datetime.now() - timedelta(days=USUAL_DAYS)
        self.env['stock.picking'].flush_model()
        self.env['mrp.production'].flush_model()
        self.env.cr.execute("""
            SELECT mo.product_id,
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY EXTRACT(EPOCH FROM (sp.date_done - mo.create_date)) / 86400.0),
                   count(*)
              FROM mrp_production mo
              JOIN stock_picking sp ON sp.sale_id = mo.sale_id AND sp.state = 'done'
              JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                                         AND spt.code = 'outgoing'
             WHERE sp.date_done >= %s AND sp.date_done > mo.create_date
             GROUP BY mo.product_id
            HAVING count(*) >= %s
        """, (since, USUAL_MIN_CASES))
        return {product_id: round(float(days), 1)
                for product_id, days, _n in self.env.cr.fetchall()}

    @api.model
    def _is_late(self, days, usual):
        """Half again as long as usual, and at least a day over."""
        if not usual:
            return False
        return days > usual * LATE_FACTOR and days > usual + LATE_MARGIN_DAYS

    @api.model
    def _late(self, live, first, started, now, usual=None):
        """Live cases that are late by the lab's own standard for their appliance.

        The oldest-cases list answers "who has waited longest"; this answers
        "who has waited longer than they should have", which is the list to
        chase. A case whose appliance has no standard yet is not judged.
        """
        usual = self._usual_days() if usual is None else usual
        rows, judged = [], 0
        for production in live:
            standard = usual.get(production.product_id.id)
            if not standard:
                continue
            judged += 1
            when = started.get(production.id)
            days = round((now - when).total_seconds() / 86400.0, 1) if when else 0.0
            if self._is_late(days, standard):
                step = first.get(production.id) or {}
                station = step.get('workcenter_id') or (0, _('No station'))
                rows.append((days - standard, days, standard, production.id, station[1]))
        rows.sort(reverse=True)
        top = rows[:TOP_LATE]
        by_id = {m.id: m for m in self.env['mrp.production'].browse([r[3] for r in top])}
        out = []
        for over, days, standard, production_id, station in top:
            production = by_id.get(production_id)
            if not production:
                continue
            order = production.sale_id
            out.append({
                'id': production_id,
                'name': production.name,
                'days': int(days),
                'usual': standard,
                'over': round(over, 1),
                'station': station,
                'patient': (order.patient or '') if order else '',
                'clinic': order.partner_id.display_name if order else '',
                'appliance': production.product_id.display_name or '',
                'order': order.name if order else '',
            })
        return {
            'count': len(rows),
            'judged': judged,
            'pct': round(len(rows) * 100.0 / judged, 1) if judged else 0.0,
            'rows': out,
            'ids': [r[3] for r in rows],
            'standards': len(usual),
        }

    # ------------------------------------------------------------------ in and out
    @api.model
    def _balance(self, weeks=WEEKS):
        """Cases in against cases out, week by week.

        In is an order confirmed; out is a delivery validated - the two events
        this office records without fail, whether or not anybody scans. The
        gap between them is the rate the backlog grows at, which is the one
        production number that decides everything else: no amount of chasing
        the oldest case helps a lab that takes in a hundred more a week than
        it sends out. (client, 2026-09-10)
        """
        today = self.env['lab.station']._lab_today()
        first = self._week_start(today) - timedelta(weeks=weeks - 1)
        start, _end = self.env['lab.station']._day_window(first)
        buckets = {first + timedelta(weeks=i): {'orders': 0, 'delivered': 0}
                   for i in range(weeks)}

        def weave(model, field, key, domain):
            for when, count in self.env[model].sudo()._read_group(
                    domain + [(field, '>=', start)], [field + ':day'], ['__count']):
                if when:
                    week = self._week_start(fields.Date.to_date(when))
                    if week in buckets:
                        buckets[week][key] += count

        weave('sale.order', 'date_order', 'orders', [('state', 'in', ('sale', 'done'))])
        weave('stock.picking', 'date_done', 'delivered',
              [('picking_type_code', '=', 'outgoing'), ('state', '=', 'done')])
        rows = []
        for week, counts in sorted(buckets.items()):
            rows.append({
                'week': fields.Date.to_string(week),
                'label': week.strftime('%d %b'),
                'orders': counts['orders'],
                'delivered': counts['delivered'],
                'net': counts['orders'] - counts['delivered'],
                'current': week == self._week_start(today),
            })
        peak = max((max(r['orders'], r['delivered']) for r in rows), default=0)
        for row in rows:
            row['orders_pct'] = round(row['orders'] / peak * 100, 1) if peak else 0.0
            row['delivered_pct'] = round(row['delivered'] / peak * 100, 1) if peak else 0.0
        # The averages leave the current week out: a Tuesday is not a week.
        complete = [r for r in rows if not r['current'] and (r['orders'] or r['delivered'])]
        n = len(complete)
        avg_in = round(sum(r['orders'] for r in complete) / n, 1) if n else 0.0
        avg_out = round(sum(r['delivered'] for r in complete) / n, 1) if n else 0.0
        return {
            'rows': rows,
            'weeks': n,
            'avg_in': avg_in,
            'avg_out': avg_out,
            'net': round(avg_in - avg_out, 1),
            'peak': peak,
        }

    @api.model
    def open_late(self):
        """The late cases, as a list to work through."""
        live, first = self._live_steps()
        now = fields.Datetime.now()
        started = {m.id: (m.date_start or m.create_date) for m in live}
        late = self._late(live, first, started, now)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Later than usual for the appliance'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', late['ids'])],
            'context': {'create': False},
        }


    # ==================================================================
    # THE FLOOR AS DECISIONS, not as descriptions. Four questions a
    # production manager answers every morning, each with the drill that
    # acts on it. (client, 2026-09-10)
    # ==================================================================
    @api.model
    def _promises(self, live=None, usual=None, now=None):
        """When each live case is DUE OUT, by the lab's own standard for its
        appliance, bucketed the way a morning is planned.

        A Hawley leaves in two days and a Twin Block in three, so "how old is
        it" is the wrong question and "when should it have gone" is the right
        one. The promise is the day the case was raised plus the median the lab
        actually delivered that appliance in over the last twelve weeks; a case
        whose appliance has no standard yet is not promised anything and is
        counted apart rather than guessed at.
        """
        now = now or fields.Datetime.now()
        if live is None:
            live, _first = self._live_steps()
        usual = self._usual_days() if usual is None else usual
        today = self.env['lab.station']._lab_today()
        buckets = [(key, label, []) for key, label in PROMISE_BUCKETS]
        by_key = {key: ids for key, _label, ids in buckets}
        unpromised = 0
        for production in live:
            _due, gap = self._due_out(production, usual, today)
            if gap is None:
                unpromised += 1
                continue
            if gap < 0:
                by_key['overdue'].append(production.id)
            elif gap == 0:
                by_key['today'].append(production.id)
            elif gap == 1:
                by_key['tomorrow'].append(production.id)
            elif gap <= 7:
                by_key['week'].append(production.id)
            else:
                by_key['later'].append(production.id)
        promised = sum(len(ids) for _k, _l, ids in buckets)
        rows = []
        for key, label, ids in buckets:
            rows.append({
                'key': key, 'label': label, 'count': len(ids),
                'pct': round(len(ids) * 100.0 / promised, 1) if promised else 0.0,
            })
        return {
            'rows': rows,
            'promised': promised,
            'unpromised': unpromised,
            'due_now': len(by_key['overdue']) + len(by_key['today']),
            'standards': len(usual),
        }

    @api.model
    @api.model
    def _due_out(self, production, usual, today=None):
        """When one case is due out, and how many days that is from today.

        THE one definition. It was written three times - the promise clock, the
        list behind each of its segments, and the station tiles - and the day
        the anchor changed in one of them the clock and its own drill-down
        disagreed, which is what `test_a_bucket_opens_the_cases_it_counted`
        caught. (client, 2026-09-12)

        The anchor is the day the case was RAISED. `date_start` holds MRP's
        PLANNED start, a future date on 1,135 of this database's 3,482 open
        jobs, so promising from it counted 113 cases as due later that were
        already overdue. (measured 2026-09-12)
        """
        standard = usual.get(production.product_id.id)
        started = production.create_date
        if not standard or not started:
            return None, None
        today = today or self.env['lab.station']._lab_today()
        due = self.env['lab.station']._lab_time(
            started + timedelta(days=standard)).date()
        return due, (due - today).days

    def _promise_ids(self, bucket):
        """The cases behind one bucket of the promise clock."""
        live, _first = self._live_steps()
        usual = self._usual_days()
        now = fields.Datetime.now()
        today = self.env['lab.station']._lab_today()
        keep = []
        for production in live:
            _due, gap = self._due_out(production, usual, today)
            if gap is None:
                continue
            key = ('overdue' if gap < 0 else 'today' if gap == 0
                   else 'tomorrow' if gap == 1 else 'week' if gap <= 7 else 'later')
            if key == bucket:
                keep.append(production.id)
        return keep

    @api.model
    def open_promise(self, bucket=None):
        """The cases due in one bucket, as a list to work through."""
        label = dict(PROMISE_BUCKETS).get(bucket or '', _('Promised'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Due out — %s', label.lower()),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', self._promise_ids(bucket or 'overdue'))],
            'context': {'create': False},
        }

    @api.model
    def _radar(self, first, days=RADAR_DAYS):
        """Every bench, in the four numbers that decide where to stand today.

        The queue as it is, what came in and went out over the last week, and
        the difference - because a bench whose queue is growing needs a
        different answer from one that is merely deep. Days to clear says which
        of them the lab is actually waiting on. (client, 2026-09-10)
        """
        Workorder = self.env['mrp.workorder']
        here, hours = {}, {}
        for row in first.values():
            station = row.get('workcenter_id') or (0, _('No station'))
            if not station[0]:
                continue
            here[station[0]] = here.get(station[0], 0) + 1
            hours[station[0]] = hours.get(station[0], 0.0) \
                + (row.get('duration_expected') or 0.0)
        if not here:
            return {'rows': [], 'in_week': 0, 'out_week': 0, 'net': 0}

        start, _end = self.env['lab.station']._day_window(
            self.env['lab.station']._lab_today() - timedelta(days=days - 1))
        # OUT: handed on from the bench in the window. IN: arrived at it - which
        # is the previous step handing over, recorded on the receiving job.
        out = {bench.id: count for bench, count in Workorder.sudo()._read_group(
            [('handed_over_at', '>=', start), ('workcenter_id', 'in', list(here))],
            ['workcenter_id'], ['__count'])}
        # ARRIVALS. A job's arrival at a bench is the PREVIOUS step handing over,
        # and "the next step" is a computed field with no column to group by - so
        # it is resolved in SQL, the same way the flow board resolves it.
        Workorder.flush_model()
        self.env.cr.execute("""
            SELECT nxt.workcenter_id, count(*)
              FROM mrp_workorder w
              JOIN LATERAL (
                  SELECT n.workcenter_id FROM mrp_workorder n
                   WHERE n.production_id = w.production_id
                     AND COALESCE(n.arch, '') = COALESCE(w.arch, '')
                     AND (n.sequence, n.id) > (w.sequence, w.id)
                   ORDER BY n.sequence, n.id LIMIT 1
              ) nxt ON TRUE
             WHERE w.handed_over_at >= %s
               AND nxt.workcenter_id = ANY(%s)
             GROUP BY nxt.workcenter_id
        """, (start, list(here)))
        came = dict(self.env.cr.fetchall())
        benches = self.env['mrp.workcenter'].with_context(
            active_test=False).browse(list(here))
        rows = []
        for bench in benches:
            people = len(bench.users | bench.head_user_ids)
            per_day = float(bench.resource_calendar_id.hours_per_day or 8.0)
            capacity = people * per_day
            queued_hours = round(hours.get(bench.id, 0.0) / 60.0, 1)
            went = out.get(bench.id, 0)
            arrived = came.get(bench.id, 0)
            rows.append({
                'id': bench.id,
                'name': bench.display_name,
                'queue': here.get(bench.id, 0),
                'hours': queued_hours,
                'people': people,
                'in_week': arrived,
                'out_week': went,
                'net': arrived - went,
                'days': round(queued_hours / capacity, 1) if capacity else None,
                'unstaffed': not people,
            })
        peak = max((abs(r['net']) for r in rows), default=0)
        for row in rows:
            row['net_pct'] = round(abs(row['net']) / peak * 100) if peak else 0
        rows.sort(key=lambda r: (r['days'] is None, -(r['days'] or 0)))
        return {
            'rows': rows[:TOP_BENCHES],
            'in_week': sum(r['in_week'] for r in rows),
            'out_week': sum(r['out_week'] for r in rows),
            'net': sum(r['net'] for r in rows),
            'days': days,
        }

    @api.model
    def _rot(self, live, first, now=None):
        """Bench against age: where the old work actually is.

        A total says the lab holds 127 cases over a month; this says which
        bench is holding them. One glance and a manager knows which queue to
        walk to, which is what a list of numbers never told them.
        """
        now = now or fields.Datetime.now()
        started = {m.id: (m.date_start or m.create_date) for m in live}
        grid, totals = {}, {key: 0 for key, *_rest in AGE_BINS}
        for production_id, step in first.items():
            station = step.get('workcenter_id') or (0, _('No station'))
            if not station[0]:
                continue
            when = started.get(production_id)
            age = max(0, (now - when).days) if when else 0
            for key, _label, low, high in AGE_BINS:
                if age >= low and (high is None or age <= high):
                    row = grid.setdefault(station[0], {'id': station[0],
                                                       'name': station[1],
                                                       'cells': {k: 0 for k, *_r in AGE_BINS},
                                                       'total': 0})
                    row['cells'][key] += 1
                    row['total'] += 1
                    totals[key] += 1
                    break
        rows = sorted(grid.values(), key=lambda r: -r['total'])[:TOP_BENCHES]
        peak = max((cell for row in rows for cell in row['cells'].values()), default=0)
        for row in rows:
            row['cells'] = [{
                'key': key, 'label': label, 'count': row['cells'][key],
                # Its weight in the grid, so the eye lands on the worst square.
                'heat': round(row['cells'][key] / peak * 100) if peak else 0,
            } for key, label, _lo, _hi in AGE_BINS]
        return {
            'rows': rows,
            'bands': [{'key': key, 'label': label, 'count': totals[key]}
                      for key, label, _lo, _hi in AGE_BINS],
        }

    @api.model
    def _waiting_doctors(self, live, usual, now=None, top=TOP_DOCTORS):
        """Whose cases are late, by clinic: the list the phone rings about.

        A doctor does not ring about a bench. They ring about their own two
        cases, and the desk that answers needs to know before they pick up
        which clinic has been waiting longest and how far past its usual.
        """
        now = now or fields.Datetime.now()
        rows = {}
        for production in live:
            order = production.sale_id
            clinic = order.partner_id if order else None
            if not clinic:
                continue
            started = production.date_start or production.create_date
            age = max(0, (now - started).days) if started else 0
            standard = usual.get(production.product_id.id)
            row = rows.setdefault(clinic.id, {
                'id': clinic.id, 'name': clinic.display_name,
                'cases': 0, 'late': 0, 'oldest': 0, 'over': 0.0})
            row['cases'] += 1
            row['oldest'] = max(row['oldest'], age)
            if standard and self._is_late(age, standard):
                row['late'] += 1
                row['over'] = max(row['over'], round(age - standard, 1))
        out = [row for row in rows.values() if row['late']]
        out.sort(key=lambda r: (-r['late'], -r['oldest']))
        return out[:top]

    @api.model
    def open_doctor(self, partner_id):
        """One clinic's live cases: what the desk opens when that doctor rings."""
        partner = self.env['res.partner'].browse(int(partner_id))
        live, _first = self._live_steps()
        keep = live.filtered(
            lambda m, p=partner: m.sale_id and m.sale_id.partner_id == p)
        return {
            'type': 'ir.actions.act_window',
            'name': _('%s — cases still here', partner.display_name),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', keep.ids)],
            'context': {'create': False},
        }

    # ------------------------------------------------------------------ quality
    @api.model
    def _redo(self, first, finished):
        """Work the lab had to start again, and why.

        A redo is the only quality signal this lab records, and it is recorded whether
        or not the floor scans - somebody has to press the button and name a reason.
        The rate is against operations finished, which is zero until the board is used;
        the count stands on its own until then.
        """
        Redo = self.env['lab.mrp.redo']
        domain = [('date', '>=', self.env['lab.station']._day_window(first)[0])]
        rows = []
        for reason, count in Redo._read_group(domain, ['reason_id'], ['__count']):
            rows.append({'id': reason.id, 'name': reason.display_name, 'count': count})
        rows.sort(key=lambda r: -r['count'])
        peak = rows[0]['count'] if rows else 0
        for row in rows:
            row['pct'] = round(row['count'] / peak * 100, 1) if peak else 0.0
        total = sum(r['count'] for r in rows)
        return {
            'reasons': rows,
            'total': total,
            'rate': round(total / finished * 100, 1) if finished else None,
            'cases': Redo.search_count(domain + [('attempt', '=', 1)]),
        }

    # ------------------------------------------------------------------ pace
    @api.model
    def _pace(self, Perf, domain):
        """Hands-on time against what the operation is costed at.

        Only jobs that carry both a timer reading and a costed duration can say
        anything; the rest are counted as 'not timed' rather than folded in as zero,
        because a job with no timer is not a job that took no time.
        """
        timed = Perf.search_count(domain + [('over_expected', '!=', False)])
        if not timed:
            return {'timed': 0, 'over': 0, 'under': 0, 'avg_over': 0.0}
        over = Perf.search_count(domain + [('over_expected', '>', 0)])
        groups = Perf._read_group(
            domain + [('over_expected', '!=', False)], [], ['over_expected:avg'])
        return {
            'timed': timed,
            'over': over,
            'under': timed - over,
            'avg_over': round((groups[0][0] if groups else 0.0) or 0.0, 1),
            'over_pct': round(over / timed * 100, 1),
        }

    @api.model
    def open_operations(self, week=None, user_id=None):
        """The operations behind a bar, in a list."""
        domain = [('handed_over_at', '!=', False)]
        name = _('Operations finished')
        if week:
            start = fields.Date.to_date(week)
            # The bars are bucketed by LOCAL day, so the list behind one must be
            # too: read as midnight UTC it began five and a half hours late and
            # opened a list the bar above it did not agree with. (client, 2026-09-10)
            Station = self.env['lab.station']
            begin, _end = Station._day_window(start)
            _begin, finish = Station._day_window(start + timedelta(days=6))
            domain += [('handed_over_at', '>=', begin),
                       ('handed_over_at', '<', finish)]
            name = _('Operations finished — week of %s', start.strftime('%d %b %Y'))
        if user_id:
            domain += self.env['mrp.workorder']._person_leaf([user_id])
        # The work orders themselves, named by case - sales order, doctor,
        # patient - rather than the performance rows, which name a job by
        # station and product only. That is what a manager reading one
        # technician's week actually wants to see. (client, 2026-09-09)
        return {
            'type': 'ir.actions.act_window', 'name': name,
            'res_model': 'mrp.workorder',
            'view_mode': 'list,form',
            # Not optional: this dict goes straight to the JS action service, which
            # reads action.views and does not fall back to view_mode.
            'views': [(self.env['lab.flow']._case_list_view(), 'list'),
                      (False, 'form')],
            'domain': domain,
            'context': {'create': False},
        }

    # ------------------------------------------------------------------ targets
    @api.model
    def get_targets(self, date_from=None, date_to=None):
        """Each technician against the target set for them, day by day.

        A count alone says nothing - eleven is a good day at one bench and a thin
        one at another - and only the manager who set the number knows which. So
        the report reads the number beside the count: per person, per day, met or
        short; and the shape of a week, which a total hides: the person who hits
        it every day and the one who hits it on Friday with a Monday of nothing.
        (client, 2026-09-09)
        """
        today = self.env['lab.station']._lab_today()
        date_from = fields.Date.to_date(date_from) if date_from else self._week_start(today)
        date_to = fields.Date.to_date(date_to) if date_to else date_from + timedelta(days=6)
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
        days = days[:62]                      # two months is a screen; more is a spreadsheet

        Target = self.env['lab.work.target'].sudo()
        Station = self.env['lab.station']
        rows = Target.search([('date', '>=', days[0]), ('date', '<=', days[-1])])
        # Recount before reading: the stored figures are the cron's, and a report
        # opened at eleven must not say what was true at seven.
        rows.action_refresh()

        # Whose week this is: anyone with a target, or anyone with a stamp.
        start, _end = Station._day_window(days[0])
        _start, end = Station._day_window(days[-1])
        window = lambda f: ['&', (f, '>=', start), (f, '<', end)]  # noqa: E731
        worked = self.env['mrp.workorder'].sudo()._day_counts_by_person(start, end)
        users = rows.mapped('user_id') | self.env['res.users'].sudo().browse(
            list(worked))
        if not users:
            return {'from': fields.Date.to_string(days[0]), 'to': fields.Date.to_string(days[-1]),
                    'days': self._day_heads(days, today), 'people': [], 'totals': None,
                    'stations': [], 'daily': [], 'daily_peak': 0}

        # {(user, day): target} - the whole-day target if set, else the bench ones summed.
        whole, benches = {}, {}
        for row in rows:
            key = (row.user_id.id, row.date)
            if row.workcenter_id:
                benches[key] = benches.get(key, 0) + row.target
            else:
                whole[key] = row.target
        # {(user, day): done}, ONE query for the whole period, by the board's own
        # rule of what counts. This was a query per day, and with the stored
        # recount above asking the same thing again it was most of the half
        # second this report took. (measured 2026-09-12)
        wanted = [day for day in days if day <= today]
        counts = self.env['mrp.workorder'].sudo()._day_counts_by_person_range(
            wanted, user_ids=users.ids)
        done = {(uid, day): n for (day, uid), n in counts.items()}

        people = []
        for user in users.sorted('name'):
            cells, t_sum, d_sum, days_set, days_met, best = [], 0, 0, 0, 0, 0
            for day in days:
                key = (user.id, day)
                target = whole.get(key, benches.get(key, 0))
                n = done.get(key, 0)
                future = day > today
                if target:
                    state = 'future' if future else ('met' if n >= target else 'short')
                else:
                    state = 'none' if not n else 'free'
                cells.append({'date': fields.Date.to_string(day), 'target': target,
                              'done': n, 'state': state,
                              'pct': min(100, round(n * 100.0 / target)) if target else 0})
                if not future:
                    t_sum += target
                    d_sum += n
                    if target:
                        days_set += 1
                        days_met += int(n >= target)
                    best = max(best, n)
            if not d_sum and not any(c['target'] for c in cells):
                continue
            # Consecutive days met, counted back from the last day that had a target.
            streak = 0
            for cell in reversed(cells):
                if cell['state'] in ('future', 'none', 'free'):
                    if cell['state'] == 'free' and streak:
                        break
                    continue
                if cell['state'] == 'met':
                    streak += 1
                else:
                    break
            worked_days = len([c for c in cells if c['done'] and c['state'] != 'future'])
            people.append({
                'id': user.id, 'name': user.name, 'days': cells,
                'target': t_sum, 'done': d_sum,
                'pct': round(d_sum * 100.0 / t_sum) if t_sum else None,
                'days_set': days_set, 'days_met': days_met, 'streak': streak,
                'best': best,
                'avg': round(d_sum / worked_days, 1) if worked_days else 0.0,
            })
        # Best achievers first; those with no target at all go last, on work done.
        people.sort(key=lambda p: (-(p['pct'] if p['pct'] is not None else -1), -p['done']))

        # The period day by day, across everybody with a target: done against
        # set, as a chart rather than a column of cells. (client, 2026-09-10)
        daily = []
        for i, day in enumerate(days):
            future = day > today
            t_day = sum(p['days'][i]['target'] for p in people)
            d_day = sum(p['days'][i]['done'] for p in people if p['days'][i]['target'])
            daily.append({
                'date': fields.Date.to_string(day), 'label': day.strftime('%a %d'),
                'target': t_day, 'done': 0 if future else d_day, 'future': future,
                'pct': (min(150, round(d_day * 100.0 / t_day)) if t_day and not future
                        else None),
                'weekend': day.weekday() >= 5,
            })
        with_target = [p for p in people if p['target']]
        totals = {
            'target': sum(p['target'] for p in with_target),
            'done': sum(p['done'] for p in people),
            'done_against': sum(p['done'] for p in with_target),
            'people': len(people),
            'people_with_target': len(with_target),
            'days_set': sum(p['days_set'] for p in with_target),
            'days_met': sum(p['days_met'] for p in with_target),
        }
        totals['pct'] = (round(totals['done_against'] * 100.0 / totals['target'])
                         if totals['target'] else None)
        return {
            'from': fields.Date.to_string(days[0]),
            'to': fields.Date.to_string(days[-1]),
            'days': self._day_heads(days, today),
            'people': people,
            'totals': totals,
            'stations': self._station_targets(rows, days, today),
            'daily': daily,
            'daily_peak': max((max(d['target'], d['done']) for d in daily), default=0),
        }

    @api.model
    def _day_heads(self, days, today):
        return [{'date': fields.Date.to_string(d), 'label': d.strftime('%a %d'),
                 'is_today': d == today, 'future': d > today,
                 'weekend': d.weekday() >= 5} for d in days]

    @api.model
    def _station_targets(self, rows, days, today):
        """Per bench: the targets set there, against what was done there."""
        Target = self.env['lab.work.target'].sudo()
        by_bench = {}
        for row in rows.filtered('workcenter_id'):
            if row.date > today:
                continue
            bench = by_bench.setdefault(row.workcenter_id.id, {
                'id': row.workcenter_id.id, 'name': row.workcenter_id.display_name,
                'target': 0, 'done': 0, 'people': set()})
            bench['target'] += row.target
            bench['people'].add(row.user_id.id)
        for bench in by_bench.values():
            workcenter = self.env['mrp.workcenter'].browse(bench['id'])
            for day in days:
                if day > today:
                    continue
                bench['done'] += sum(Target._done_for(
                    day, list(bench['people']), workcenter).values())
            bench['people'] = len(bench['people'])
            bench['pct'] = round(bench['done'] * 100.0 / bench['target']) if bench['target'] else 0
        return sorted(by_bench.values(), key=lambda b: -b['pct'])

    @api.model
    def open_person_cases(self, user_id, date_from, date_to):
        """The work orders behind one person's row - named by case."""
        Station = self.env['lab.station']
        start, _end = Station._day_window(fields.Date.to_date(date_from))
        _start, end = Station._day_window(fields.Date.to_date(date_to))
        user = self.env['res.users'].browse(int(user_id))
        window = lambda f: ['&', (f, '>=', start), (f, '<', end)]  # noqa: E731
        return {
            'type': 'ir.actions.act_window',
            'name': _('%(who)s — %(a)s to %(b)s', who=user.name,
                      a=fields.Date.to_date(date_from).strftime('%d %b'),
                      b=fields.Date.to_date(date_to).strftime('%d %b')),
            'res_model': 'mrp.workorder',
            'domain': self.env['mrp.workorder']._person_leaf([user.id])
                      + ['|', '|'] + window('bench_assigned_at')
                      + window('accepted_at') + window('handed_over_at'),
            'views': [(self.env['lab.flow']._case_list_view(), 'list'), (False, 'form')],
            'context': {'create': False},
        }

    # ------------------------------------------------------------------ drills
    @api.model
    def open_backlog(self, workcenter_id=None, age=None):
        """The live cases behind a backlog bar or an ageing band.

        Computed as ids rather than expressed as a domain: "the case's FIRST unfinished
        step is at this station" cannot be written as one, and a domain that merely
        matched any unfinished step would open a list four times too long.
        """
        live, first = self._live_steps()
        now = fields.Datetime.now()
        started = {m.id: (m.date_start or m.create_date) for m in live}
        name = _('Live cases')
        keep = []
        for production_id, step in first.items():
            if workcenter_id:
                station = step.get('workcenter_id') or (0, '')
                if station[0] != int(workcenter_id):
                    continue
            if age:
                when = started.get(production_id)
                days = max(0, (now - when).days) if when else 0
                match = next((b for b in AGE_BINS if b[0] == age), None)
                if not match or days < match[2] or \
                        (match[3] is not None and days > match[3]):
                    continue
            keep.append(production_id)
        if workcenter_id:
            station = self.env['mrp.workcenter'].browse(int(workcenter_id))
            name = _('Waiting at %s', station.display_name)
        if age:
            label = next((b[1] for b in AGE_BINS if b[0] == age), '')
            name = _('%(what)s - %(band)s', what=name, band=label.lower())
        return {
            'type': 'ir.actions.act_window', 'name': name,
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', keep)],
            'context': {'create': False},
        }

    @api.model
    def open_redo(self, reason_id=None, weeks=WEEKS):
        """The redo log behind a reason bar."""
        weeks = max(1, min(int(weeks or WEEKS), 26))
        first = self._week_start(self.env['lab.station']._lab_today()) - \
            timedelta(weeks=weeks - 1)
        domain = [('date', '>=', self.env['lab.station']._day_window(first)[0])]
        name = _('Work started again')
        if reason_id:
            domain.append(('reason_id', '=', int(reason_id)))
            reason = self.env['lab.redo.reason'].browse(int(reason_id))
            name = _('Started again - %s', reason.display_name)
        return {
            'type': 'ir.actions.act_window', 'name': name,
            'res_model': 'lab.mrp.redo',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'context': {'create': False},
        }

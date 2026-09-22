# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import SQL

AGEING_HOURS = 24
# The two priorities this lab jumps the queue for, worst first. A count of
# them sends a supervisor looking; the board says which cases they are.
# (client, 2026-09-10)
RUSH = ('emergency', 'urgent')
RUSH_ROWS = 12
# Twelve is a screenful; a list longer than that is a report, not a call to action.
ATTENTION_ROWS = 12


class LabFlow(models.AbstractModel):
    """The whole lab floor on one screen.

    The station board answers "what is on MY bench". Nobody could answer "where is the
    work piling up", which is the only question that improves a lab: a station board per
    bench shows six calm queues and hides the one that is drowning.

    So this is the pipeline end to end — every station side by side, in routing order,
    with what is waiting at each. The bottleneck is not a judgement anyone has to make;
    it is simply the tallest column.
    """
    _name = 'lab.flow'
    _description = 'Production Flow'

    @api.model
    def _live_workorder_domain(self):
        """Open operations of orders GENUINELY still in the lab.

        `state not in (done, cancel)` alone is the largest lie in this database:
        89.6% of "open" manufacturing orders sit on a sales order whose delivery
        is already signed for (see report.lab.floor). Counting their
        operations put 21,856 on this board while the hub tile said 2,140, made
        every stage's total equal its "waiting" equal its "old" (a migrated
        ghost is never accepted and always ancient), and flooded "Sitting
        longest" with appliances that have been on the doctor's shelf since
        April. Every figure on this board now stands on the same live set the
        floor sheets print. (client, 2026-09-02)
        """
        live = self.env['mrp.production'].sudo()._search(
            self.env['report.lab.floor']._live_domain())
        return [('state', 'not in', ('done', 'cancel')),
                ('production_id', 'in', live)]

    @api.model
    def get_flow(self, day=None):
        """The floor, for one day - today unless asked. (client, 2026-09-09)

        Everything live (queues, bottleneck, urgent, sitting longest) is the
        floor as it stands this minute, whichever day is asked for: last
        Tuesday's queues are gone. What follows the day is what HAPPENED on
        it - taken, finished, by whom, when, how long - which is what a
        manager reading back a day actually wants.
        """
        Workorder = self.env['mrp.workorder']
        now = fields.Datetime.now()
        today = self.env['lab.station']._lab_today()
        day_date = fields.Date.to_date(day) if day else today
        if day_date > today:
            day_date = today
        day_start, day_end = self._day_window(day_date)
        is_today = day_date == today
        ageing_before = now - timedelta(hours=AGEING_HOURS)
        # RESOLVED ONCE, then handed round as ids. "Genuinely still in the lab"
        # is a subquery over deliveries, and every helper on this board is
        # bounded by it: asking it eight times cost eight hundred milliseconds
        # of the same work. Nine thousand ids cost 130 ms to fetch and turn
        # every later question from 110 ms into 20. (client, 2026-09-10)
        open_domain = [('id', 'in', Workorder.search(
            self._live_workorder_domain()).ids)]

        # GROUPED COUNTS, not a hundred thousand records. Reading every open job
        # into memory to count them took three seconds on the client's floor — on a
        # board that refreshes itself every ninety seconds, on a phone. The database
        # counts rows far better than Python does, and the screen only ever shows the
        # numbers. (client, 2026-08-26)
        #
        # And ONE scan for all five, not five: the live set is defined by a
        # subquery over deliveries, so each of these cost a hundred milliseconds
        # of the same work. Conditional counts ask the five questions in one
        # pass. (client, 2026-09-10)
        start_today, _end_today = self._day_window(today)
        wip, waiting, stale, arrived, urgent_by_station = self._station_counts(
            open_domain, ageing_before, start_today)

        # active_test off so a station that was archived while work was still sitting
        # at it cannot make that work vanish from the only screen that would show it.
        # Where each live case actually IS: its first unfinished step, once.
        # `wip` above counts every open step against its station, which puts a
        # case routed through four benches in four queues at once - true of
        # the rows, false about the floor. The queue drawn is the real one.
        # One live read for the whole board: the standing queues and the rush
        # report both want it, and it is the most expensive thing here.
        # (client, 2026-09-10)
        Floor = self.env['report.lab.floor']
        live_now = self.env['mrp.production'].sudo().search(Floor._live_domain())
        live_steps = Floor._first_open_step(live_now)
        standing, standing_waiting, oldest_at, stuck_at = self._standing(
            open_domain, ageing_before, live_steps)
        process = self._station_process(day_start, day_end)
        # What each station did on the day: took, finished, and how long a
        # finished step took on average.
        finished_day = dict(Workorder._read_group(
            [('handed_over_at', '>=', day_start), ('handed_over_at', '<', day_end)],
            ['workcenter_id'], ['__count']))
        minutes_day = {wc: avg for wc, avg in Workorder._read_group(
            [('handed_over_at', '>=', day_start), ('handed_over_at', '<', day_end)],
            ['workcenter_id'], ['duration:avg'])}
        taken_day = dict(Workorder._read_group(
            [('accepted_at', '>=', day_start), ('accepted_at', '<', day_end)],
            ['workcenter_id'], ['__count']))
        stations = []
        for wc in self.env['mrp.workcenter'].with_context(active_test=False).search([]):
            here = wip.get(wc.id, 0)
            if not here and not wc.active:
                continue
            people = wc.users | wc.head_user_ids
            stations.append({
                'id': wc.id,
                'name': wc.display_name,
                'wip': here,
                'here': standing.get(wc.id, 0),
                'here_waiting': standing_waiting.get(wc.id, 0),
                'oldest_h': self._hours_since(oldest_at.get(wc.id)),
                'stuck': stuck_at.get(wc.id, 0),
                'finished_day': finished_day.get(wc, 0),
                'taken_day': taken_day.get(wc, 0),
                'avg_min_day': int(round(minutes_day.get(wc) or 0)),
                # How the station PROCESSES work on the day: the wait before a
                # step is started, the time a step takes, who was there and when,
                # and whether the queue grew or shrank. (client, 2026-09-09)
                **process.get(wc.id, self._no_process()),
                'waiting': waiting.get(wc.id, 0),
                'stale': stale.get(wc.id, 0),
                'people': len(people),
                'arrived_today': arrived.get(wc.id, 0),
                'urgent': urgent_by_station.get(wc.id, 0),
                # Nobody posted to a station with work at it is a queue that cannot
                # move, which looks identical to a slow one until you ask.
                'unstaffed': bool(here) and not people,
            })

        people = self._people(day_start, day_end, open_domain, day=day_date)
        peak = max((s['here'] for s in stations), default=0)
        for s in stations:
            s['pct'] = round(s['here'] / peak * 100, 1) if peak else 0.0
            s['is_bottleneck'] = bool(peak) and s['here'] == peak and peak > 1

        return {
            'stations': stations,
            'totals': {
                # Jobs and operations are different numbers on purpose: the hub
                # tile counts manufacturing orders, a station queues operations.
                # The banner says both so the two screens can never look like
                # they disagree again.
                'jobs': self.env['mrp.production'].sudo().search_count(
                    self.env['report.lab.floor']._live_domain()),
                'wip': sum(wip.values()),
                'waiting': sum(waiting.values()),
                'stale': sum(stale.values()),
                'ageing_hours': AGEING_HOURS,
            },
            'attention': self._attention(ageing_before, open_domain),
            'throughput': self._throughput(),
            'adoption': self._adoption(open_domain, sum(wip.values()),
                                       start_today),
            # Urgent and emergency work, as a report rather than a count: which
            # cases they are, where each is standing and how long it has been
            # there. A number alone tells a supervisor to go looking.
            # (client, 2026-09-10)
            'urgent': self._rush(open_domain, day_start, day_end,
                                 live=live_now, first=live_steps),
            # The 21,099 finished-but-open orders are not the floor's work,
            # but they are the floor's DEBT: until the office closes them,
            # every report on this database needs a live-set carve-out.
            'closeout': self.env['mrp.production'].sudo().search_count(
                self.env['report.lab.floor']._closeout_domain()),
            # The day being read back. (client, 2026-09-09)
            'day': self._day(day_date, day_start, day_end, is_today, now),
            'people': people,
            # The day against the targets set for it, as one chip: how many
            # of the people given a number reached it. (client, 2026-09-10)
            'targets': self._targets_summary(people),
            'hours': self._hours(day_start, day_end),
            'pace': self._pace(day_date, day_start, now) if is_today else None,
            'as_of': self.env['lab.station']._lab_time(now).strftime('%H:%M'),
            'standing_total': sum(standing.values()),
            'stuck_total': sum(stuck_at.values()),
            # Where the work went on the day: hand-overs from station to station.
            'flows': self._flows(day_start, day_end),
        }

    # ------------------------------------------------------------ the day
    @api.model
    def _day_window(self, day):
        """The lab's day as a UTC pair - the station board's, so the two agree."""
        return self.env['lab.station']._day_window(day)

    @api.model
    def _hours_since(self, value):
        if not value:
            return 0
        return max(0, int((fields.Datetime.now() - value).total_seconds() // 3600))

    @api.model
    def _station_counts(self, open_domain, ageing_before, start_today):
        """Five per-station counts over the live set, in one pass.

        Everything on this board is bounded by "orders genuinely still in the
        lab", which is a subquery over deliveries - so asking five separate
        grouped questions paid for that subquery five times. Returns
        (wip, waiting, stale, arrived, rush), each {workcenter_id: count}.
        """
        Workorder = self.env['mrp.workorder']
        query = Workorder._search(open_domain)
        Workorder.flush_model()
        self.env['mrp.production'].flush_model()
        self.env['sale.order'].flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT wo.workcenter_id,
                   count(*) AS wip,
                   count(*) FILTER (WHERE wo.accepted_at IS NULL) AS waiting,
                   count(*) FILTER (WHERE wo.create_date < %(ageing)s) AS stale,
                   count(*) FILTER (WHERE wo.handed_over_at >= %(today)s) AS arrived,
                   count(*) FILTER (WHERE so.priority = ANY(%(rush)s)) AS rush
              FROM mrp_workorder wo
              LEFT JOIN mrp_production mo ON mo.id = wo.production_id
              LEFT JOIN sale_order so ON so.id = mo.sale_id
             WHERE wo.id IN %(ids)s AND wo.workcenter_id IS NOT NULL
             GROUP BY wo.workcenter_id
            """, ageing=ageing_before, today=start_today, rush=list(RUSH),
            ids=query.subselect()))
        wip, waiting, stale, arrived, rush = {}, {}, {}, {}, {}
        for station, n_wip, n_wait, n_stale, n_arrived, n_rush in self.env.cr.fetchall():
            wip[station] = n_wip
            waiting[station] = n_wait
            stale[station] = n_stale
            arrived[station] = n_arrived
            rush[station] = n_rush
        return wip, waiting, stale, arrived, rush

    @api.model
    def _standing(self, open_domain, ageing_before=None, first=None):
        """Per station: live cases whose FIRST open step is there, how many of
        those nobody has taken, the oldest of them, and how many have stood
        untaken past the ageing line - the stuck ones."""
        if first is None:
            Floor = self.env['report.lab.floor']
            first = Floor._first_open_step(
                self.env['mrp.production'].sudo().search(Floor._live_domain()))
        ids = [row['id'] for row in first.values()]
        if not ids:
            return {}, {}, {}, {}
        Workorder = self.env['mrp.workorder']
        counts = {wc.id: n for wc, n in Workorder._read_group(
            [('id', 'in', ids)], ['workcenter_id'], ['__count'])}
        waiting = {wc.id: n for wc, n in Workorder._read_group(
            [('id', 'in', ids), ('accepted_at', '=', False)],
            ['workcenter_id'], ['__count'])}
        oldest = {wc.id: when for wc, when in Workorder._read_group(
            [('id', 'in', ids)], ['workcenter_id'], ['create_date:min'])}
        stuck = {}
        if ageing_before:
            stuck = {wc.id: n for wc, n in Workorder._read_group(
                [('id', 'in', ids), ('accepted_at', '=', False),
                 ('create_date', '<', ageing_before)],
                ['workcenter_id'], ['__count'])}
        return counts, waiting, oldest, stuck

    @api.model
    def _no_process(self):
        return {'in_day': 0, 'out_day': 0, 'net_day': 0, 'wait_min': 0,
                'cycle_min': 0, 'people_day': 0, 'first': '', 'last': '',
                'over_day': 0}

    @api.model
    def _station_process(self, start, end):
        """How each station processed work on the day.

        A queue count says how much is standing there; none of it says how the
        bench WORKS. These do: what came in and went out (and so whether the
        queue grew), how long a case waited at the bench before somebody
        started it, how long a step took once started, how many people worked
        the bench, and when the first and last thing happened. The wait is
        measured from the moment the previous step handed the case on - the
        moment it arrived - not from when the order was created.
        (client, 2026-09-09)
        """
        self.env['mrp.workorder'].flush_model()
        self.env.cr.execute("""
            SELECT w.workcenter_id,
                   count(*) FILTER (WHERE w.accepted_at >= %(s)s AND w.accepted_at < %(e)s) AS in_day,
                   count(*) FILTER (WHERE w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s) AS out_day,
                   avg(EXTRACT(EPOCH FROM (w.accepted_at - COALESCE(prev.handed_over_at, w.create_date))) / 60)
                       FILTER (WHERE w.accepted_at >= %(s)s AND w.accepted_at < %(e)s) AS wait_min,
                   avg(EXTRACT(EPOCH FROM (w.handed_over_at - w.accepted_at)) / 60)
                       FILTER (WHERE w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s
                              AND w.accepted_at IS NOT NULL) AS cycle_min,
                   count(DISTINCT w.bench_user_id)
                       FILTER (WHERE w.bench_user_id IS NOT NULL) AS people_day,
                   min(w.accepted_at) FILTER (WHERE w.accepted_at >= %(s)s AND w.accepted_at < %(e)s) AS first_at,
                   max(w.handed_over_at) FILTER (WHERE w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s) AS last_at,
                   count(*) FILTER (WHERE w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s
                                    AND w.duration_expected > 0 AND w.duration > w.duration_expected) AS over_day
              FROM mrp_workorder w
              LEFT JOIN LATERAL (
                  SELECT p.handed_over_at FROM mrp_workorder p
                   WHERE p.production_id = w.production_id
                     AND COALESCE(p.arch, '') = COALESCE(w.arch, '')
                     AND (p.sequence, p.id) < (w.sequence, w.id)
                   ORDER BY p.sequence DESC, p.id DESC LIMIT 1
              ) prev ON TRUE
             WHERE w.workcenter_id IS NOT NULL
               AND (w.accepted_at >= %(s)s AND w.accepted_at < %(e)s
                    OR w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s)
             GROUP BY w.workcenter_id
        """, {'s': start, 'e': end})
        clock = lambda v: self.env['lab.station']._clock(v)  # noqa: E731
        out = {}
        for wc_id, in_day, out_day, wait_min, cycle_min, people, first_at, last_at, over in \
                self.env.cr.fetchall():
            out[wc_id] = {
                'in_day': in_day, 'out_day': out_day, 'net_day': in_day - out_day,
                'wait_min': int(round(wait_min or 0)),
                'cycle_min': int(round(cycle_min or 0)),
                'people_day': people,
                'first': clock(first_at), 'last': clock(last_at),
                'over_day': over,
            }
        return out

    @api.model
    def _flows(self, start, end, limit=12):
        """Where the work went on the day: hand-overs from station to station.

        The process, as it actually ran: not the routing on paper but the
        paths cases took. The busiest paths first; a step that was the last
        one shows as going to "finished". The next step is found in SQL -
        `next_workcenter_id` is computed and has no column to group by.

        The next step OF THE SAME ARCH, and whatever its state now: the day's
        hand-over is the event, so a next bench that has since finished too is
        still where the work went - filtering it out credited the lane to the
        step after. Only a cancelled step is passed over; it never held the job
        by the time anyone reads this. (review, 2026-09-15)
        """
        self.env['mrp.workorder'].flush_model()
        self.env.cr.execute("""
            SELECT w.workcenter_id, nxt.workcenter_id, count(*)
              FROM mrp_workorder w
              LEFT JOIN LATERAL (
                  SELECT n.workcenter_id FROM mrp_workorder n
                   WHERE n.production_id = w.production_id
                     AND COALESCE(n.arch, '') = COALESCE(w.arch, '')
                     AND n.state <> 'cancel'
                     AND (n.sequence, n.id) > (w.sequence, w.id)
                   ORDER BY n.sequence, n.id LIMIT 1
              ) nxt ON TRUE
             WHERE w.handed_over_at >= %(s)s AND w.handed_over_at < %(e)s
               AND w.workcenter_id IS NOT NULL
             GROUP BY w.workcenter_id, nxt.workcenter_id
             ORDER BY count(*) DESC
             LIMIT %(limit)s
        """, {'s': start, 'e': end, 'limit': limit})
        found = self.env.cr.fetchall()
        ids = {wc for wc, nxt, _ in found} | {nxt for wc, nxt, _ in found if nxt}
        names = {w.id: w.display_name for w in
                 self.env['mrp.workcenter'].sudo().with_context(active_test=False).browse(list(ids))}
        rows = [{'from_id': wc, 'from': names.get(wc, ''),
                 'to_id': nxt or False, 'to': names.get(nxt, '') if nxt else '',
                 'count': n} for wc, nxt, n in found]
        peak = rows[0]['count'] if rows else 0
        for r in rows:
            r['pct'] = round(r['count'] * 100.0 / peak) if peak else 0
        return rows

    @api.model
    def _day(self, day_date, start, end, is_today, now):
        """What happened on the day, in the lab's own units."""
        Workorder = self.env['mrp.workorder']
        Production = self.env['mrp.production'].sudo()
        finished = Workorder.search_count(
            [('handed_over_at', '>=', start), ('handed_over_at', '<', end)])
        delivered = 0
        if 'lab.delivery' in self.env.registry:
            delivered = self.env['lab.delivery'].sudo().search_count(
                [('state', '=', 'delivered'), ('direction', '=', 'out'),
                 ('delivered_datetime', '>=', start), ('delivered_datetime', '<', end)])
        else:
            delivered = self.env['stock.picking'].sudo().search_count(
                [('picking_type_id.code', '=', 'outgoing'), ('state', '=', 'done'),
                 ('date_done', '>=', start), ('date_done', '<', end)])
        return {
            'date': fields.Date.to_string(day_date),
            'label': fields.Date.to_date(day_date).strftime('%A, %d %B'),
            'is_today': is_today,
            'today': fields.Date.to_string(self.env['lab.station']._lab_today()),
            'taken': Workorder.search_count(
                [('accepted_at', '>=', start), ('accepted_at', '<', end)]),
            'finished': finished,
            'urgent_finished': Workorder.search_count(
                [('handed_over_at', '>=', start), ('handed_over_at', '<', end),
                 ('production_id.sale_id.priority', 'in', RUSH)]),
            'new_cases': Production.search_count(
                [('create_date', '>=', start), ('create_date', '<', end),
                 ('state', '!=', 'cancel')]),
            'delivered': delivered,
            'redos': self.env['lab.mrp.redo'].sudo().search_count(
                [('create_date', '>=', start), ('create_date', '<', end)]),
            'minutes': int(round(sum(Workorder.search(
                [('handed_over_at', '>=', start), ('handed_over_at', '<', end)])
                .mapped('duration')))),
        }

    @api.model
    def _people(self, start, end, open_domain, limit=40, day=None):
        """Each technician's day: taken, finished, minutes on the bench, when
        they started and stopped, and what they still hold.

        The question the floor asked for by name - "how many works by each
        employee, and the time". (client, 2026-09-09)

        Both people on a job are counted: the technician who carried it out and,
        at a bench that records one, the technician who finished it - the same
        rule the station chips and the targets count by, kept in one place at
        `mrp.workorder._person_credit`. What is NOT shared is the time: the
        minutes on a job are the bench's, so they stay with the technician
        rather than being counted twice. (client, 2026-09-10)
        """
        Workorder = self.env['mrp.workorder']
        Workorder.flush_model()
        self.env.cr.execute(SQL("""
            SELECT p.person,
                   count(*) FILTER (WHERE p.as_tech
                                    AND (wo.accepted_at >= %(s)s AND wo.accepted_at < %(e)s
                                     OR wo.bench_assigned_at >= %(s)s AND wo.bench_assigned_at < %(e)s)) AS taken,
                   count(*) FILTER (WHERE wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s) AS finished,
                   coalesce(sum(wo.duration) FILTER (WHERE p.as_tech
                                    AND wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s), 0) AS minutes,
                   count(*) FILTER (WHERE p.as_tech
                                    AND wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s
                                    AND wo.duration > wo.duration_expected AND wo.duration_expected > 0) AS over,
                   least(min(wo.accepted_at) FILTER (WHERE p.as_tech AND wo.accepted_at >= %(s)s AND wo.accepted_at < %(e)s),
                         min(wo.bench_assigned_at) FILTER (WHERE p.as_tech AND wo.bench_assigned_at >= %(s)s AND wo.bench_assigned_at < %(e)s)) AS first_at,
                   max(wo.handed_over_at) FILTER (WHERE wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s) AS last_at,
                   count(DISTINCT wo.workcenter_id) FILTER (WHERE wo.handed_over_at >= %(s)s AND wo.handed_over_at < %(e)s) AS stations,
                   count(*) FILTER (WHERE NOT p.as_tech) AS finished_for_others
              FROM mrp_workorder wo
              %(credit)s
             GROUP BY p.person
             ORDER BY finished DESC, taken DESC
             LIMIT %(limit)s
        """, s=start, e=end, credit=Workorder._person_credit(start, end), limit=limit))
        rows = self.env.cr.fetchall()
        # The targets set for the day, and the board's own count against
        # them - taken or finished, once - so this table, the station chips
        # and the report can never disagree about who reached what.
        targets, done_for = {}, {}
        if day is not None:
            Target = self.env['lab.work.target'].sudo()
            targets = Target.day_targets(day)
            if targets:
                done_for = Target._done_for(day, list(targets))
        if not rows and not targets:
            return []
        users = {u.id: u.name for u in self.env['res.users'].sudo().browse(
            list({r[0] for r in rows} | set(targets)))}
        holding = {u.id: n for u, n in self.env['mrp.workorder']._read_group(
            open_domain + [('bench_user_id', 'in', [r[0] for r in rows])],
            ['bench_user_id'], ['__count'])}
        clock = lambda v: self.env['lab.station']._clock(v)  # noqa: E731
        out = []
        for (uid, taken, finished, minutes, over, first_at, last_at, stations,
             finished_for_others) in rows:
            minutes = int(round(minutes or 0))
            # Minutes per job is measured over the jobs those minutes belong to -
            # the ones this person carried out - not over the finishes as well,
            # which would divide a technician's time by somebody else's work.
            own = finished - finished_for_others
            out.append({
                'id': uid,
                'name': users.get(uid, ''),
                'taken': taken,
                'finished': finished,
                # Of those finishes, the ones done for somebody else's job:
                # the row still counts them, and says so.
                'finished_as_finisher': finished_for_others,
                'minutes': minutes,
                'avg_min': int(round(minutes / own)) if own else 0,
                'over': over,
                'first': clock(first_at),
                'last': clock(last_at),
                'stations': stations,
                'holding': holding.get(uid, 0),
                **self._against_target(uid, targets, done_for),
            })
        # A person given a target who has not touched a job is exactly who the
        # manager opened this to see: a row of zeros, not an absence.
        seen = {r[0] for r in rows}
        for uid in sorted((u for u in targets if u not in seen),
                          key=lambda u: users.get(u, '')):
            out.append({
                'id': uid, 'name': users.get(uid, ''), 'taken': 0, 'finished': 0,
                'finished_as_finisher': 0, 'minutes': 0, 'avg_min': 0, 'over': 0,
                'first': '', 'last': '', 'stations': 0, 'holding': 0,
                **self._against_target(uid, targets, done_for),
            })
        return out

    @api.model
    def _against_target(self, uid, targets, done_for):
        target = targets.get(uid, 0)
        done = done_for.get(uid, 0) if target else 0
        return {
            'target': target,
            'done': done,
            'target_met': bool(target) and done >= target,
            'target_pct': min(100, round(done * 100.0 / target)) if target else 0,
        }

    @api.model
    def _targets_summary(self, people):
        """The day's targets in one line: of those given a number, how many
        reached it, and the floor's total against the total set."""
        given = [p for p in people if p.get('target')]
        return {
            'set': len(given),
            'met': len([p for p in given if p['target_met']]),
            'target': sum(p['target'] for p in given),
            'done': sum(p['done'] for p in given),
        }

    @api.model
    def _hours(self, start, end):
        """The day's rhythm: what was taken and finished in each hour.

        A total says how much; this says WHEN - the late start, the lunch
        dip, the three o'clock push, the bench that goes quiet at four.
        """
        Workorder = self.env['mrp.workorder']
        Station = self.env['lab.station']
        buckets = [{'h': h, 'label': '%02d' % h, 'taken': 0, 'finished': 0}
                   for h in range(24)]

        def hour_of(value):
            return Station._lab_time(value).hour

        for wo in Workorder.search_read(
                [('handed_over_at', '>=', start), ('handed_over_at', '<', end)],
                ['handed_over_at'], limit=5000):
            buckets[hour_of(wo['handed_over_at'])]['finished'] += 1
        for wo in Workorder.search_read(
                [('accepted_at', '>=', start), ('accepted_at', '<', end)],
                ['accepted_at'], limit=5000):
            buckets[hour_of(wo['accepted_at'])]['taken'] += 1
        # The working day, not the clock: the hours before the first act and
        # after the last are not part of the picture.
        active = [b for b in buckets if b['taken'] or b['finished']]
        if not active:
            return []
        lo, hi = active[0]['h'], active[-1]['h']
        lo, hi = max(0, min(lo, 8)), min(23, max(hi, 18))
        peak = max(max(b['taken'], b['finished']) for b in buckets) or 1
        return [dict(b, pct_taken=round(b['taken'] * 100.0 / peak),
                     pct_finished=round(b['finished'] * 100.0 / peak))
                for b in buckets[lo:hi + 1]]

    @api.model
    def _pace(self, day_date, day_start, now, days=7):
        """Finished so far today against the usual by this hour.

        "14 finished" means nothing at eleven in the morning without knowing
        that a normal day has 22 by then. The usual is the mean of the last
        seven days that finished anything, measured to the same clock time.
        """
        Workorder = self.env['mrp.workorder']
        elapsed = now - day_start
        so_far = Workorder.search_count(
            [('handed_over_at', '>=', day_start), ('handed_over_at', '<', now)])
        # The last seven days to the same clock time, counted in ONE pass: this
        # was a search per day, eight round trips over a hundred thousand rows
        # for a single chip. (client, 2026-09-10)
        first_start, _ = self._day_window(day_date - timedelta(days=days))
        tz = self.env['lab.station']._lab_tz().zone
        Workorder.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT (handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS day,
                   count(*)
              FROM mrp_workorder
             WHERE handed_over_at >= %(from_)s AND handed_over_at < %(to_)s
               AND (handed_over_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::time
                   < ((%(day_start)s AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::time
                      + %(elapsed)s)
             GROUP BY 1
            """, tz=tz, from_=first_start, to_=day_start,
            day_start=day_start, elapsed=elapsed))
        samples = [n for _day, n in self.env.cr.fetchall() if n]
        usual = round(sum(samples) / len(samples)) if samples else 0
        return {'so_far': so_far, 'usual': usual, 'days': len(samples),
                'delta': so_far - usual,
                'pct': round(so_far * 100.0 / usual) if usual else 0}

    @api.model
    def action_open_person(self, user_id, day=None, over_only=False):
        """One person's day, as the work orders it was made of.

        `over_only` is the drill behind the "N over time" the row already
        printed: the panel said how many of somebody's steps ran past their
        expected time and then gave no way of seeing WHICH, so the number was
        only ever an accusation. (client, 2026-09-19)
        """
        day_date = fields.Date.to_date(day) if day else \
            self.env['lab.station']._lab_today()
        start, end = self._day_window(day_date)
        user = self.env['res.users'].browse(int(user_id))
        if over_only:
            return {
                'type': 'ir.actions.act_window',
                'name': self.env._('%(who)s — over time, %(day)s', who=user.name,
                                   day=day_date.strftime('%d %b')),
                'res_model': 'mrp.workorder',
                'domain': [('id', 'in', self._over_time_ids(user.id, start, end))],
                # Its own list, which shows Expected beside Real: a step that
                # ran long is only readable next to what it was meant to take.
                'views': [(self.env.ref(
                    'lab_workcenter_scan.view_workorder_list_over_time').id, 'list'),
                    (False, 'form')],
            }
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('%(who)s — %(day)s', who=user.name,
                               day=day_date.strftime('%d %b')),
            'res_model': 'mrp.workorder',
            # Their own work and the jobs they finished for others, because that
            # is what the row counted. (client, 2026-09-10)
            'domain': self.env['mrp.workorder']._person_leaf([user.id]) + [
                '|', '|',
                '&', ('accepted_at', '>=', start), ('accepted_at', '<', end),
                '&', ('bench_assigned_at', '>=', start), ('bench_assigned_at', '<', end),
                '&', ('handed_over_at', '>=', start), ('handed_over_at', '<', end)],
            'views': [(self._case_list_view(), 'list'), (False, 'form')],
        }

    @api.model
    def _over_time_ids(self, user_id, start, end):
        """The steps this person finished in the window that ran past expectation.

        Exactly the condition the people panel counts with - technician (not
        finisher), handed over inside the day, and an actual duration past a
        duration_expected that was set at all. Kept beside that SQL on purpose:
        if the count and the list ever disagree, the number stops being worth
        printing.

        The comparison is field-to-field, which a domain cannot express, so the
        day's candidates are narrowed in SQL and weighed in Python. One person's
        day is a handful of rows.
        """
        candidates = self.env['mrp.workorder'].search([
            ('bench_user_id', '=', int(user_id)),
            ('handed_over_at', '>=', start), ('handed_over_at', '<', end),
            ('duration_expected', '>', 0),
        ])
        return candidates.filtered(
            lambda w: w.duration > w.duration_expected).ids

    @api.model
    def _adoption(self, open_domain, wip, start_today):
        """Is the floor actually scanning yet?

        Right now every stage reads "all waiting / all old" because the board
        went live before the habit did. That is a fact worth SAYING - and
        measuring, because the number that moves first as the lab adopts the
        scanner is this one, weeks before the queues themselves change shape.
        """
        # FOUR questions about the same rows, asked once: conditional counts over
        # one scan rather than four searches of a live set that is tens of
        # thousands of rows. (client, 2026-09-10)
        Workorder = self.env['mrp.workorder']
        query = Workorder._search(open_domain)
        Workorder.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT count(*) FILTER (WHERE accepted_at IS NOT NULL),
                   count(*) FILTER (WHERE handed_over_at >= %(today)s),
                   count(*) FILTER (WHERE accepted_at >= %(today)s),
                   count(DISTINCT workcenter_id) FILTER (
                       WHERE accepted_at >= %(today)s OR handed_over_at >= %(today)s)
              FROM mrp_workorder WHERE id IN %(ids)s
            """, today=start_today, ids=query.subselect()))
        accepted, moved_today, accepted_today, active = self.env.cr.fetchone()
        return {
            'accepted': accepted,
            'accepted_pct': round(accepted * 100.0 / wip, 1) if wip else 0.0,
            'moved_today': moved_today,
            'accepted_today': accepted_today,
            'stations_active_today': active,
        }

    @api.model
    def _rush(self, open_domain, day_start=None, day_end=None, limit=RUSH_ROWS,
              live=None, first=None):
        """Every urgent and emergency case on the floor, and where it stands.

        A count sends a supervisor looking; this says which cases they are,
        which bench each is at, whether anybody has taken it, and how long it
        has been waiting - one row per CASE, at the step it is actually at, so
        a job routed through four benches is not four rows. Emergency ranks
        above urgent and, within each, the longest wait comes first.
        (client, 2026-09-10)
        """
        if live is None or first is None:
            Floor = self.env['report.lab.floor']
            live = self.env['mrp.production'].search(
                Floor._live_domain() + [('sale_id.priority', 'in', RUSH)])
            first = Floor._first_open_step(live)
        else:
            # The board already read the floor; only the rush cases are wanted.
            live = live.filtered(
                lambda m: m.sale_id and m.sale_id.priority in RUSH)
        now = fields.Datetime.now()
        by_priority = {key: 0 for key in RUSH}
        by_station, rows = {}, []
        waiting = 0
        step_ids = [row['id'] for row in first.values()]
        steps = {wo.id: wo for wo in self.env['mrp.workorder'].browse(step_ids)}
        for production in live:
            step = steps.get((first.get(production.id) or {}).get('id'))
            if step is None:
                continue
            order = production.sale_id
            priority = order.priority if order else 'urgent'
            by_priority[priority] = by_priority.get(priority, 0) + 1
            station = step.workcenter_id
            if station:
                cell = by_station.setdefault(station.id, {
                    'id': station.id, 'name': station.display_name, 'count': 0})
                cell['count'] += 1
            started = production.date_start or production.create_date
            days = max(0, (now - started).days) if started else 0
            taken = bool(step.accepted_at)
            if not taken:
                waiting += 1
            rows.append({
                'id': step.id,
                'production_id': production.id,
                'production': production.name,
                'order': order.name if order else '',
                'patient': (order.patient or '') if order else '',
                'clinic': order.partner_id.display_name if order else '',
                'appliance': production.product_id.display_name or '',
                'station': station.display_name or self.env._('No station'),
                'station_id': station.id or False,
                'operation': step.name or '',
                'priority': priority,
                'emergency': priority == 'emergency',
                'days': days,
                'taken': taken,
                'who': step.bench_user_id.name or '',
            })
        # Emergency above urgent; within each, whoever has waited longest.
        rows.sort(key=lambda r: (0 if r['emergency'] else 1, -r['days']))
        finished = 0
        if day_start and day_end:
            finished = self.env['mrp.workorder'].search_count([
                ('handed_over_at', '>=', day_start), ('handed_over_at', '<', day_end),
                ('production_id.sale_id.priority', 'in', RUSH)])
        return {
            'total': len(rows),
            'emergency': by_priority.get('emergency', 0),
            'urgent': by_priority.get('urgent', 0),
            'waiting': waiting,
            'on_bench': len(rows) - waiting,
            'oldest': max((r['days'] for r in rows), default=0),
            'finished_today': finished,
            'rows': rows[:limit],
            'more': max(0, len(rows) - limit),
            'stations': sorted(by_station.values(), key=lambda c: -c['count'])[:6],
            'ids': [r['production_id'] for r in rows],
        }

    @api.model
    def action_open_rush(self, priority=None):
        """The urgent and emergency cases still in the lab, as a list."""
        Floor = self.env['report.lab.floor']
        wanted = [priority] if priority in RUSH else list(RUSH)
        ids = self.env['mrp.production'].search(
            Floor._live_domain() + [('sale_id.priority', 'in', wanted)]).ids
        label = self.env._('Emergency') if priority == 'emergency' else (
            self.env._('Urgent') if priority == 'urgent' else self.env._('Urgent and emergency'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('%s cases still in the lab', label),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', ids)],
            'context': {'create': False},
        }

    @api.model
    def action_open_closeout(self):
        """The office worklist: orders the doctor already has, still open
        here. Shrinking this pile is how the database stops needing carve-outs."""
        ids = self.env['mrp.production'].sudo().search(
            self.env['report.lab.floor']._closeout_domain()).ids
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Delivered but never closed'),
            'res_model': 'mrp.production',
            'domain': [('id', 'in', ids)],
            'views': [(False, 'list'), (False, 'form')],
        }

    @api.model
    def _attention(self, ageing_before, open_domain):
        """Jobs a manager has to do something about, most urgent first.

        Ordered by how long they have sat, not by priority: an urgent job started an
        hour ago is fine, and a routine one untouched since Tuesday is not.

        One row PER ORDER: a two-appliance case has an operation per appliance
        and the same age on each, so without the fold the list was one order
        printed twelve times and eleven real problems went unseen.

        Folded in SQL, before the limit. Folding the oldest seventy-two
        OPERATIONS in Python filled the list with a handful of cases on a floor
        where one case carries ten of them, and a case older than those
        seventy-two rows never appeared at all. (review, 2026-09-15)
        """
        now = fields.Datetime.now()
        Workorder = self.env['mrp.workorder']
        query = Workorder._search(open_domain + [('create_date', '<', ageing_before)])
        Workorder.flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT id FROM (
                SELECT DISTINCT ON (wo.production_id) wo.id, wo.create_date
                  FROM mrp_workorder wo
                 WHERE wo.id IN %s
                 ORDER BY wo.production_id, wo.create_date, wo.id
            ) oldest
             ORDER BY create_date, id
             LIMIT %s
            """, query.subselect(), ATTENTION_ROWS))
        stuck = Workorder.browse([row[0] for row in self.env.cr.fetchall()])
        rows = []
        for wo in stuck:
            hours = int(((now - (wo.create_date or now)).total_seconds()) // 3600)
            order = wo.production_id.sale_id if wo.production_id else False
            rows.append({
                'id': wo.id,
                'name': wo.display_name,
                'patient': (order.patient if order else '') or wo.production_id.name,
                # The sales order is how the rest of the lab says this job out loud:
                # the office quotes it on the phone, the delivery note carries it, the
                # doctor asks about it. A patient name alone is not enough to look a
                # case up anywhere else. (client, 2026-08-26)
                'order': (order.name if order else '') or '',
                'mo': wo.production_id.name or '',
                'station': wo.workcenter_id.display_name,
                'hours': hours,
                'accepted': bool(wo.accepted_at),
                'with_person': wo.bench_user_id.name or '',
                'urgent': bool(order) and order.priority == 'urgent',
            })
        return rows

    @api.model
    def _throughput(self):
        """What actually left the floor. WIP without throughput says how busy the lab
        looks, never how much it finished."""
        Workorder = self.env['mrp.workorder']
        today = self.env['lab.station']._lab_today()
        start_today, _end = self._day_window(today)
        week_start, _end = self._day_window(today - timedelta(days=6))
        return {
            'today': Workorder.search_count([('handed_over_at', '>=', start_today)]),
            'week': Workorder.search_count([('handed_over_at', '>=', week_start)]),
            'finished_today': self.env['mrp.production'].search_count([
                ('state', '=', 'done'),
                ('date_finished', '>=', start_today)]),
        }

    @api.model
    def _case_list_view(self):
        """The work-order list named by case, not by bench.

        Both drills come from a screen that has already said which station or
        which person this is, so a Work Centre column repeats what the title
        says and the case - order, doctor, patient - is what was missing.
        The id, not the xml id: a view ref that has been deleted must not take
        the drill down with it. (client, 2026-09-09)
        """
        view = self.env.ref('lab_workcenter_scan.view_workorder_list_case',
                            raise_if_not_found=False)
        return view.id if view else False

    @api.model
    def action_open_station(self, workcenter_id):
        """The station's queue, LIVE work only — the drill must hold the same
        money the number did, or the first click teaches everyone the board
        lies."""
        # ids, not the subquery: this domain travels to the browser, and a
        # Query object does not serialise.
        live = self.env['mrp.production'].sudo().search(
            self.env['report.lab.floor']._live_domain()).ids
        return {
            'type': 'ir.actions.act_window',
            'name': self.env['mrp.workcenter'].browse(workcenter_id).display_name,
            'res_model': 'mrp.workorder',
            'domain': [('state', 'not in', ('done', 'cancel')),
                       ('workcenter_id', '=', workcenter_id),
                       ('production_id', 'in', live)],
            'views': [(self._case_list_view(), 'list'), (False, 'form')],
        }

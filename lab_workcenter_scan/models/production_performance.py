# -*- coding: utf-8 -*-
"""What the floor actually did, one row per job.

A lab that cannot answer "how long does an Acrylisation take, and who is quick at it"
prices its work by feel and blames whoever is nearest when a case is late. Everything
here is derived from stamps the board already writes as people work — nothing has to be
filled in afterwards, because a productivity figure that depends on somebody remembering
to type it is a productivity figure nobody has.

Three different clocks, and they answer different questions:

  waiting   arrived at the bench -> a technician accepted it.  The LEAD's number: work
            sitting in a pile is work nobody was given.
  working   accepted -> handed on.  Wall clock, so it contains lunch and the end of the
            shift: this is how long the DOCTOR waited for that step.
  clocked   what Odoo's own timers recorded between start and finish (minutes).  Hands
            actually on the job — the only one of the three that is a rate.

Reading `clocked` as "how hard somebody worked" is the classic mistake, so the views
label it "hands-on" and put waiting beside it: a bench with a huge waiting figure and a
small clocked one is not a slow technician, it is a queue nobody handed out.
"""
from datetime import timedelta

from odoo import api, fields, models, tools


class LabProductionPerformance(models.Model):
    _name = 'lab.production.performance'
    _description = 'Production Performance'
    _auto = False
    _order = 'handed_over_at desc, id desc'
    _rec_name = 'workorder_name'

    workorder_id = fields.Many2one('mrp.workorder', 'Job', readonly=True)
    workorder_name = fields.Char('Operation', readonly=True)
    production_id = fields.Many2one('mrp.production', 'Manufacturing Order',
                                    readonly=True)
    workcenter_id = fields.Many2one('mrp.workcenter', 'Station', readonly=True)
    product_id = fields.Many2one('product.product', 'Appliance', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)

    # Who. `bench_user_id` is who the lead gave it to; `accepted_by_id` is who actually
    # picked it up. They are usually the same person and the gap between them is worth
    # seeing when they are not.
    bench_user_id = fields.Many2one('res.users', 'Technician', readonly=True)
    # The technician who FINISHED it, where the bench records that separately.
    # `finisher_other_id` is the same person only when they are not also the
    # technician - which is what lets a person's work be counted in both roles
    # without counting a job they did and finished twice. (client, 2026-09-10)
    finisher_user_id = fields.Many2one('res.users', 'Finishing Technician', readonly=True)
    finisher_other_id = fields.Many2one(
        'res.users', 'Finished for Somebody Else', readonly=True)
    accepted_by_id = fields.Many2one('res.users', 'Accepted By', readonly=True)
    handed_over_by_id = fields.Many2one('res.users', 'Finished By', readonly=True)
    bench_assigned_by_id = fields.Many2one('res.users', 'Given Out By', readonly=True)

    # The case behind the job, in the words the rest of the lab uses.
    sale_id = fields.Many2one('sale.order', 'Sales Order', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Clinic', readonly=True)
    team_id = fields.Many2one('crm.team', 'Sales Route', readonly=True)
    patient = fields.Char('Patient', readonly=True)
    priority = fields.Selection(
        # `emergency` comes from lab_order_control's selection_add on the sale
        # order; the view copies the raw value, so it has to be listed here too.
        [('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent'),
         ('emergency', 'Emergency')],
        string='Urgency', readonly=True)

    state = fields.Selection(
        [('blocked', 'Blocked'), ('ready', 'To Do'), ('progress', 'In Progress'),
         ('done', 'Finished'), ('cancel', 'Cancelled')],
        string='Status', readonly=True)
    stage = fields.Selection(
        [('queued', 'Not given out'), ('given', 'Given out, not started'),
         ('bench', 'On the bench'), ('finished', 'Handed on')],
        string='Where It Got To', readonly=True,
        help="Derived from the stamps the station board writes, not from the "
             "manufacturing status: a job can be 'ready' in Odoo and still be sitting "
             "in a pile nobody was given.")

    arrived_at = fields.Datetime('Arrived', readonly=True)
    assigned_at = fields.Datetime('Given Out', readonly=True)
    accepted_at = fields.Datetime('Accepted', readonly=True)
    handed_over_at = fields.Datetime('Handed On', readonly=True)

    waiting_hours = fields.Float(
        'Waiting (h)', readonly=True, aggregator='avg', digits=(16, 2),
        help="Arrived at the station until a technician accepted it. The lead's "
             "number: work sitting in a pile is work nobody was given.")
    working_hours = fields.Float(
        'On the Bench (h)', readonly=True, aggregator='avg', digits=(16, 2),
        help="Accepted until handed on — wall clock, so it includes breaks and "
             "overnight. This is how long the doctor waited for this step.")
    total_hours = fields.Float(
        'Total (h)', readonly=True, aggregator='avg', digits=(16, 2),
        help="Arrived until handed on: waiting plus bench time.")
    clocked_minutes = fields.Float(
        'Hands-on (min)', readonly=True, aggregator='avg', digits=(16, 1),
        help="What Odoo's own timers recorded between start and finish. The only one "
             "of the three that is a rate — but read it beside Waiting, or a bench "
             "with a queue nobody handed out looks like a slow technician.")
    expected_minutes = fields.Float(
        'Expected (min)', readonly=True, aggregator='avg', digits=(16, 1))
    over_expected = fields.Float(
        'Over Expected (min)', readonly=True, aggregator='avg', digits=(16, 1),
        help="Hands-on time beyond what the operation is costed at. Positive is slower "
             "than planned; negative is faster.")

    jobs = fields.Integer('Jobs', readonly=True, aggregator='sum',
                          help="Always 1, so any grouping counts jobs by summing it.")
    finished = fields.Integer('Finished', readonly=True, aggregator='sum')
    qty = fields.Float('Quantity', readonly=True, aggregator='sum')

    def init(self):
        # EXTRACT(EPOCH ...) / 3600.0 rather than an interval: the client needs to
        # average and compare these, and an interval will not go through a pivot.
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    wo.id                                    AS id,
                    wo.id                                    AS workorder_id,
                    wo.name                                  AS workorder_name,
                    wo.production_id                         AS production_id,
                    wo.workcenter_id                         AS workcenter_id,
                    wo.bench_user_id                         AS bench_user_id,
                    wo.finisher_user_id                      AS finisher_user_id,
                    CASE WHEN wo.finisher_user_id IS DISTINCT FROM wo.bench_user_id
                         THEN wo.finisher_user_id END        AS finisher_other_id,
                    wo.accepted_by_id                        AS accepted_by_id,
                    wo.handed_over_by_id                     AS handed_over_by_id,
                    wo.bench_assigned_by_id                  AS bench_assigned_by_id,
                    wo.state                                 AS state,
                    wo.create_date                           AS arrived_at,
                    wo.bench_assigned_at                     AS assigned_at,
                    wo.accepted_at                           AS accepted_at,
                    wo.handed_over_at                        AS handed_over_at,
                    mo.product_id                            AS product_id,
                    mo.company_id                            AS company_id,
                    mo.sale_id                               AS sale_id,
                    so.partner_id                            AS partner_id,
                    so.team_id                               AS team_id,
                    so.patient                               AS patient,
                    so.priority                              AS priority,
                    COALESCE(wo.qty_produced, 0.0)           AS qty,
                    1                                        AS jobs,
                    CASE WHEN wo.handed_over_at IS NOT NULL THEN 1 ELSE 0 END
                                                             AS finished,
                    CASE
                        WHEN wo.handed_over_at IS NOT NULL THEN 'finished'
                        WHEN wo.accepted_at IS NOT NULL    THEN 'bench'
                        WHEN wo.bench_user_id IS NOT NULL  THEN 'given'
                        ELSE 'queued'
                    END                                      AS stage,
                    -- Waiting is measured to acceptance where that happened, and to NOW
                    -- where it has not: a job that has sat unaccepted for three days is
                    -- the single most useful number on this report, and leaving it null
                    -- until somebody finally picks it up hides exactly the case the
                    -- lab needs to see.
                    CASE WHEN wo.state NOT IN ('done', 'cancel')
                              AND wo.accepted_at IS NULL
                         THEN EXTRACT(EPOCH FROM (now() - wo.create_date)) / 3600.0
                         WHEN wo.accepted_at IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (wo.accepted_at - wo.create_date))
                              / 3600.0
                    END                                      AS waiting_hours,
                    CASE WHEN wo.accepted_at IS NOT NULL AND wo.handed_over_at IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (wo.handed_over_at - wo.accepted_at))
                              / 3600.0
                         WHEN wo.accepted_at IS NOT NULL
                              AND wo.state NOT IN ('done', 'cancel')
                         THEN EXTRACT(EPOCH FROM (now() - wo.accepted_at)) / 3600.0
                    END                                      AS working_hours,
                    CASE WHEN wo.handed_over_at IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (wo.handed_over_at - wo.create_date))
                              / 3600.0
                         WHEN wo.state NOT IN ('done', 'cancel')
                         THEN EXTRACT(EPOCH FROM (now() - wo.create_date)) / 3600.0
                    END                                      AS total_hours,
                    wo.duration                              AS clocked_minutes,
                    wo.duration_expected                     AS expected_minutes,
                    CASE WHEN wo.duration_expected > 0 AND wo.duration > 0
                         THEN wo.duration - wo.duration_expected
                    END                                      AS over_expected
                FROM mrp_workorder wo
                JOIN mrp_production mo ON mo.id = wo.production_id
                LEFT JOIN sale_order so ON so.id = mo.sale_id
            )
        """)

    @api.model
    def station_summary(self, date_from=None, date_to=None, workcenter_ids=None,
                        user_ids=None):
        """The floor in numbers, for the PDF and for anything else that asks.

        Grouped in SQL rather than read into Python: this runs over every work order the
        lab has ever booked, and a report that pulls a hundred thousand rows into memory
        to average four columns is a report that times out in the month it matters.
        """
        domain = self._summary_domain(date_from, date_to, workcenter_ids, user_ids)

        fields_ = ['jobs:sum', 'finished:sum', 'waiting_hours:avg',
                   'working_hours:avg', 'total_hours:avg', 'clocked_minutes:avg']
        by_station = self._read_group(domain, ['workcenter_id'], fields_)
        # People rows are the people ASKED FOR. The domain holds every job one of
        # them did or finished, and grouping that by technician printed the
        # colleague whose job a selected polisher finished. (review, 2026-09-15)
        by_person = self._read_group(
            domain + [('bench_user_id', 'in', user_ids) if user_ids
                      else ('bench_user_id', '!=', False)],
            ['bench_user_id'], fields_)
        # A polisher who finishes other people's jobs did that work too, so it is
        # counted under their name. Only the counts: the minutes on a job belong
        # to the bench that spent them. (client, 2026-09-10)
        finished_for = self._read_group(
            domain + [('finisher_other_id', 'in', user_ids) if user_ids
                      else ('finisher_other_id', '!=', False)],
            ['finisher_other_id'], ['jobs:sum', 'finished:sum'])
        totals = self._read_group(domain, [], fields_)
        people = {g[0].id: self._row(g[0], g[1:]) for g in by_person}
        for user, jobs, finished in finished_for:
            row = people.get(user.id)
            if row:
                row['jobs'] += jobs or 0
                row['finished'] += finished or 0
            else:
                row = self._row(user, None)
                row['jobs'] = jobs or 0
                row['finished'] = finished or 0
                people[user.id] = row
            row['finished_for_others'] = finished or 0
        return {
            'stations': [self._row(g[0], g[1:]) for g in by_station],
            'people': sorted(people.values(), key=lambda r: -r['jobs']),
            'totals': self._row(None, totals[0]) if totals else self._row(None, None),
        }

    @api.model
    def _summary_domain(self, date_from=None, date_to=None, workcenter_ids=None,
                        user_ids=None):
        """The jobs a summary covers - ONE definition for the PDF and for the
        analysis it opens, which filtered on the technician alone and so showed
        fewer jobs than the page it came from. (review, 2026-09-15)"""
        domain = self._period_domain(date_from, date_to)
        if workcenter_ids:
            domain.append(('workcenter_id', 'in', list(workcenter_ids)))
        if user_ids:
            domain += ['|', ('bench_user_id', 'in', list(user_ids)),
                       ('finisher_other_id', 'in', list(user_ids))]
        return domain

    @api.model
    def _period_domain(self, date_from, date_to):
        """A job belongs to the period it was HANDED ON in.

        Not the period it arrived in: otherwise last month's report keeps changing every
        time an old job is finally finished, and two runs of the same report disagree.
        """
        domain = [('handed_over_at', '!=', False)]
        if date_from:
            domain.append(('handed_over_at', '>=', self._start_of(date_from)))
        if date_to:
            domain.append(('handed_over_at', '<=', self._end_of(date_to)))
        return domain

    @api.model
    def _start_of(self, date_from):
        """Midnight of that day HERE, as UTC.

        `Datetime.to_datetime(date)` is midnight UTC, which in this lab is
        half past five in the morning: a report for today silently dropped
        everything handed on before 05:30 and everything after 18:30 - the
        evening shift's whole output - and only ever agreed with the floor in
        the middle of the day. Same window the station board counts by.
        (client, 2026-09-10)
        """
        start, _end = self.env['lab.station']._day_window(
            fields.Date.to_date(date_from))
        return start

    @api.model
    def _end_of(self, date_to):
        """Inclusive of the last day: a report run "to the 31st" that stops at midnight
        on the 31st silently drops a day's work. In the reader's own zone."""
        _start, end = self.env['lab.station']._day_window(
            fields.Date.to_date(date_to))
        return end - timedelta(seconds=1)

    @api.model
    def _row(self, record, values):
        jobs, finished, waiting, working, total, clocked = values or (0, 0, 0, 0, 0, 0)
        return {
            'id': record.id if record else False,
            'name': record.display_name if record else '',
            'jobs': jobs or 0,
            'finished': finished or 0,
            'waiting_hours': round(waiting or 0.0, 1),
            'working_hours': round(working or 0.0, 1),
            'total_hours': round(total or 0.0, 1),
            'clocked_minutes': round(clocked or 0.0, 1),
            # Of the jobs above, the ones finished for somebody else. Filled in
            # by station_summary where there are any.
            'finished_for_others': 0,
        }

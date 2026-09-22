# -*- coding: utf-8 -*-
"""The floor on paper.

Two documents, because a lab asks two different questions and one sheet cannot answer
both:

  Summary       what the floor did over a period — per station and per person, with the
                waiting time beside the hands-on time so neither can be read alone.
  Hand-out      what one technician has in front of them right now, one page each, for
                a bench with no tablet. It is a work list, not a report.

Both are built from the same aggregates as the on-screen analysis, so a printed figure
and a screen figure never disagree.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Enough to see the shape of the problem without printing a phone book.
ATTENTION_LIMIT = 15
SHEET_LIMIT = 60


class LabProductionReportWizard(models.TransientModel):
    _name = 'lab.production.report.wizard'
    _description = 'Production Report'

    report_kind = fields.Selection(
        [('summary', 'Floor summary — what was produced, and how fast'),
         ('handout', 'Hand-out sheets — one page per technician')],
        default='summary', required=True, string='Print')

    date_from = fields.Date(
        'From', required=True,
        default=lambda s: s.env['lab.station']._lab_today() - timedelta(days=29))
    date_to = fields.Date('To', required=True,
                          default=lambda s: s.env['lab.station']._lab_today())
    workcenter_ids = fields.Many2many('mrp.workcenter', string='Stations',
                                      help="Leave empty for the whole floor.")
    user_ids = fields.Many2many('res.users', string='Technicians',
                                help="Leave empty for everybody.")

    @api.constrains('date_from', 'date_to')
    def _check_period(self):
        for wizard in self:
            if wizard.date_from > wizard.date_to:
                raise UserError(_("The 'from' date is after the 'to' date."))

    def action_print(self):
        self.ensure_one()
        if self.report_kind == 'handout':
            return self.env.ref(
                'lab_workcenter_scan.action_report_production_handout'
            ).report_action(self)
        return self.env.ref(
            'lab_workcenter_scan.action_report_production_summary'
        ).report_action(self)

    def action_open_analysis(self):
        """The same period, on screen, where it can be sliced further."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'lab_workcenter_scan.action_production_performance')
        action['domain'] = self.env['lab.production.performance']._summary_domain(
            self.date_from, self.date_to,
            self.workcenter_ids.ids or None, self.user_ids.ids or None)
        action['context'] = {'search_default_by_station': 1}
        return action

    # ------------------------------------------------------------------ the numbers
    def summary_data(self):
        """Everything the summary PDF prints, in one place so the template stays dumb."""
        self.ensure_one()
        Perf = self.env['lab.production.performance']
        summary = Perf.station_summary(
            self.date_from, self.date_to,
            self.workcenter_ids.ids or None, self.user_ids.ids or None)

        # What is still stuck, right now. A period report that only counts finished work
        # flatters a floor that is quietly filling up: the jobs that never got handed on
        # are exactly the ones missing from every average above.
        stuck_domain = [('state', 'not in', ('done', 'cancel')),
                        ('handed_over_at', '=', False)]
        if self.workcenter_ids:
            stuck_domain.append(('workcenter_id', 'in', self.workcenter_ids.ids))
        stuck = Perf._read_group(
            stuck_domain, ['workcenter_id'],
            ['jobs:sum', 'waiting_hours:avg', 'waiting_hours:max'])
        open_rows = [{
            'name': wc.display_name if wc else _('No station'),
            'jobs': jobs or 0,
            'avg_waiting': round(avg or 0.0, 1),
            'worst_waiting': round(worst or 0.0, 1),
        } for wc, jobs, avg, worst in stuck]
        open_rows.sort(key=lambda r: r['worst_waiting'], reverse=True)

        oldest = Perf.search(
            stuck_domain + [('waiting_hours', '>', 24)],
            order='waiting_hours desc', limit=ATTENTION_LIMIT)

        stations = sorted(summary['stations'],
                          key=lambda r: r['total_hours'], reverse=True)
        people = sorted(summary['people'], key=lambda r: r['jobs'], reverse=True)
        return {
            'totals': summary['totals'],
            'stations': stations,
            'people': people,
            'open_rows': open_rows[:ATTENTION_LIMIT],
            'open_total': sum(r['jobs'] for r in open_rows),
            'oldest': oldest,
            # The slowest step is the one worth attacking, and it is not a judgement:
            # it is simply the tallest bar.
            'slowest': stations[0] if stations else None,
        }

    def handout_data(self):
        """One block per technician: what they are holding, oldest first."""
        self.ensure_one()
        Workorder = self.env['mrp.workorder']
        people = self.user_ids
        if not people:
            stations = self.workcenter_ids or self.env['mrp.workcenter'].search([])
            people = stations.mapped('users') | stations.mapped('head_user_ids')
        blocks = []
        for user in people.sorted('name'):
            domain = [('bench_user_id', '=', user.id),
                      ('state', 'not in', ('done', 'cancel'))]
            if self.workcenter_ids:
                domain.append(('workcenter_id', 'in', self.workcenter_ids.ids))
            jobs = Workorder.search(domain, order='create_date', limit=SHEET_LIMIT)
            if jobs:
                blocks.append({'user': user, 'jobs': jobs,
                               'total': Workorder.search_count(domain)})
        return blocks


class LabProductionReportSummary(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_production_summary'
    _description = 'Production Summary Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizards = self.env['lab.production.report.wizard'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'lab.production.report.wizard',
            'docs': wizards,
            'summary': {w.id: w.summary_data() for w in wizards},
        }


class LabProductionReportHandout(models.AbstractModel):
    _name = 'report.lab_workcenter_scan.report_production_handout'
    _description = 'Production Hand-out Sheets'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizards = self.env['lab.production.report.wizard'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'lab.production.report.wizard',
            'docs': wizards,
            'blocks': {w.id: w.handout_data() for w in wizards},
        }

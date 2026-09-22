# -*- coding: utf-8 -*-
"""The executive's day as one record, approved twice.

The split of the manager role (2026-08-29) is a split of two different questions
that one person used to answer alone:

* **Did the day actually happen as claimed?** Attendance, kilometres, GPS at the
  door, visits closed properly. That is the *Operational Manager's* question, and
  it comes first - there is no point judging the quality of work that did not
  happen.
* **Was the day worth anything?** New doors opened, cases brought back, payments
  chased, the clinic relationship moved forward. That is the *Marketing Manager's*
  question, and it is only worth asking about a day operations has vouched for.

So one record - the day sheet - crosses two desks in order: submitted -> ops
approved -> approved. Sent back at either desk with a reason, it returns to the
executive to fix and resubmit.

Nothing on the sheet is typed by the executive except a note. Every figure is
collected from the records the day already produced - visits, trips, case slips,
orders, attendance, deliveries - because a summary someone retypes is a summary
that disagrees with the detail. The sheet is a SNAPSHOT taken at submission:
what the approvers signed is what they saw, even if a record moves later.
"""
import logging
from datetime import timedelta

from .local_day import local_midnight_utc, tz_name
from .lab_visit import metres_between
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount, html_escape

_logger = logging.getLogger(__name__)

STATES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('ops_approved', 'Ops Approved'),
    # Read and passed on, NOT approved. A day with issues that a desk signed
    # looked exactly like a clean day signed, so the desk after it and the
    # administrator could not tell it had been looked at. The desk now says
    # what it checked instead of approving. (client, 2026-09-15)
    ('ops_checked', 'Ops Checked · Issues Noted'),
    ('approved', 'Approved'),
    ('checked', 'Checked · Issues Noted'),
    ('sent_back', 'Sent Back'),
    # Raised BY a desk TO the administrator, as opposed to sent back to the
    # executive. A manager had two verbs - sign it, or return it to the person
    # who wrote it - and neither says "this day has a problem somebody above me
    # needs to see". The administrator learned about those only by reading a
    # chatter nobody opens. (client, 2026-09-05)
    ('flagged', 'With the Administrator'),
]

# What KIND of problem, not just that there is one. A free-text complaint is a
# story about one day; a coded one is a pattern across a route, an executive or
# a month - which is the difference between the administrator answering flags
# and fixing what causes them.
FLAG_KINDS = [
    ('location', 'Location does not support the visits'),
    ('timing', 'Times look entered in bulk'),
    ('travel', 'Travel or odometer does not match the round'),
    ('money', 'Collection or cash does not add up'),
    ('activity', 'Quality of the activities'),
    ('attendance', 'Attendance not kept properly'),
    ('other', 'Something else'),
]

MANAGER_GROUP = 'lab_fieldwork.group_fieldwork_manager'

# What an approver types before pressing a button: the reason, the kind, the
# score. A stand-in on a desk is not a manager, so these are the only fields
# the cover lets them write - everything else still moves through the action
# methods that check who is signing.
APPROVER_INPUT_FIELDS = frozenset((
    'send_back_reason', 'flag_kind', 'flag_reason',
    'marketing_score', 'marketing_remark', 'ops_check_note', 'check_note'))

# Where a sheet waits for the marketing desk, where it is on any desk, and
# where it is finished - each with its "checked, issues noted" twin.
MARKETING_QUEUE = ('ops_approved', 'ops_checked')
ON_A_DESK = ('submitted',) + MARKETING_QUEUE
SIGNED = ('approved', 'checked')

# How many previous sheets the streak walk is willing to read. A streak longer
# than this is reported as "60+", which is already the point made.
STREAK_HORIZON = 60

# HOW THIS LAB SAYS A DAY OUT LOUD: "11th Sept 2026", not "2026-09-10".
# Not babel's locale format, which gives "Sep 11, 2026" on this database's
# en_US and reads as an American date to a desk that writes the day first -
# the office asked for this one by example. (client, 2026-09-11)
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec')


def day_label(day):
    """A date as the desk writes it: 1st, 2nd, 3rd, 11th, 21st Sept 2026."""
    if not day:
        return ''
    number = day.day
    # 11th, 12th and 13th are the three the naive rule gets wrong.
    suffix = 'th' if 11 <= number % 100 <= 13 else \
        {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return '%s%s %s %s' % (number, suffix, MONTHS[day.month - 1], day.year)


class LabDailyUpdate(models.Model):
    _name = 'lab.daily.update'
    _description = 'Field Work Day Sheet'
    _order = 'date desc, user_id'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    user_id = fields.Many2one(
        'res.users', string='Executive', required=True, index=True, tracking=True,
        default=lambda self: self.env.user,
        domain="[('fw_is_field_person', '=', True)]")
    date = fields.Date(required=True, index=True, tracking=True,
                       default=fields.Date.context_today)
    # The same day, written the way the desk says it. Shown wherever the date
    # is only being READ; the field above stays for the day it is set.
    # NOT 'Date' - the field above already carries that label, and two of them
    # is a trap in a filter or an export. (2026-09-12)
    date_label = fields.Char(string='Date (in words)',
                             compute='_compute_date_label')
    state = fields.Selection(STATES, default='draft', required=True, tracking=True,
                             copy=False, index=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    # --- raised to the administrator
    flag_kind = fields.Selection(FLAG_KINDS, string='Issue', tracking=True,
                                 copy=False)
    flag_reason = fields.Text('What the desk saw', copy=False)
    flagged_by_id = fields.Many2one('res.users', 'Raised By', readonly=True,
                                    copy=False)
    flagged_at = fields.Datetime('Raised At', readonly=True, copy=False)
    flagged_desk = fields.Selection(
        [('ops', 'Operations'), ('marketing', 'Marketing')],
        string='Raised From', readonly=True, copy=False,
        help="Which desk raised it - and the desk it goes back to once the "
             "administrator has answered.")
    flag_answer = fields.Text("Administrator's answer", copy=False)
    flag_closed_by_id = fields.Many2one('res.users', 'Answered By',
                                        readonly=True, copy=False)
    flag_closed_at = fields.Datetime('Answered At', readonly=True, copy=False)
    # How often this executive has been raised lately. A first flag and a
    # fourth in three months are different conversations, and the approver
    # holding the sheet is the one who needs to know which they are having.
    flag_history_count = fields.Integer(
        'Flags in 90 Days', compute='_compute_flag_history')
    note = fields.Text(
        'Executive Note',
        help="The one thing the figures cannot say - what the day felt like, what "
             "a doctor said, what needs somebody's attention.")

    # ------------------------------------------------------------- day facts
    # A snapshot, not a live compute: what was approved must stay what was seen.
    # Refreshed on create, on demand while editable, and again at submission.
    visit_count = fields.Integer('Visits', readonly=True)
    visit_done_count = fields.Integer('Visits Completed', readonly=True)
    gps_ok_count = fields.Integer('At the Door (GPS)', readonly=True)
    gps_far_count = fields.Integer('Away from Clinic', readonly=True)
    # A visit whose clinic carries no pin cannot be judged either way. It used to
    # fall between the two counts above and vanish, so a day where NOTHING could be
    # checked reported the same "0 away from clinic" as a day where every check
    # passed - silence read as a pass. Counted in its own right instead.
    # (client, 2026-09-05)
    gps_nopin_count = fields.Integer('GPS Not Checked', readonly=True)
    minutes_at_clinics = fields.Integer('Minutes at Clinics', readonly=True)
    case_count = fields.Integer('Case Slips', readonly=True)
    reworks_collected = fields.Integer(
        'Reworks Collected', readonly=True,
        help="The executive's count of reworks (cases returned for correction) "
             "taken over the day's finished visits.")
    cases_counted = fields.Integer(
        'Cases Counted', readonly=True,
        help="The executive's own count of cases taken, summed over the day's "
             "finished visits. Compare with Case Slips: counted but not "
             "registered is a question for the executive.")
    order_count = fields.Integer('Orders', readonly=True)
    order_value = fields.Monetary('Order Value', readonly=True,
                                  currency_field='currency_id')
    collected = fields.Monetary('Collected', readonly=True,
                                currency_field='currency_id')
    km = fields.Float('Distance (km)', readonly=True)
    delivery_count = fields.Integer('Deliveries', readonly=True)
    new_clinic_count = fields.Integer('New Clinics', readonly=True)
    attendance_in = fields.Char('Started', readonly=True)
    attendance_out = fields.Char('Ended', readonly=True)
    attendance_auto_closed = fields.Boolean(readonly=True)

    # ------------------------------------------------------------- anomalies
    # Words, not colour alone - the same rule the rest of the module follows.
    flag_gps = fields.Boolean('GPS Exception', readonly=True)
    flag_open_work = fields.Boolean('Left Open', readonly=True)
    flag_zero_visits = fields.Boolean('No Visits', readonly=True)
    flag_km_outlier = fields.Boolean('Distance Outlier', readonly=True)
    flag_attendance = fields.Boolean('Attendance Exception', readonly=True)
    anomaly_note = fields.Text('Why it is an exception', readonly=True)
    is_clean = fields.Boolean(
        'Clean Day', readonly=True, index=True,
        help="Every check passed: attendance closed by the person, all visits "
             "closed with an outcome, GPS at the door, distance in the normal "
             "range. Clean days are what Approve All Clean signs in one tap.")

    # ------------------------------------------------------------- approvals
    submitted_at = fields.Datetime(readonly=True, copy=False)
    ops_approved_by_id = fields.Many2one('res.users', 'Ops Approved By',
                                         readonly=True, copy=False)
    ops_approved_at = fields.Datetime(readonly=True, copy=False)
    ops_check_note = fields.Text(
        'What Operations Checked', copy=False, tracking=True,
        help="Written instead of approving a day that has issues.")
    check_note = fields.Text(
        'What Marketing Checked', copy=False, tracking=True,
        help="Written instead of approving the activities of a day that has "
             "issues.")
    approved_by_id = fields.Many2one('res.users', 'Activities Approved By',
                                     readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)
    send_back_reason = fields.Text(copy=False, tracking=True)
    escalated = fields.Boolean(readonly=True, copy=False,
                               help="The pending-approval SLA fired for this "
                                    "sheet - warned once, not every day.")
    nudge_count = fields.Integer(
        readonly=True, copy=False,
        help="Times an approver asked a question about this day without "
             "sending it back. Twice with nothing changed is a send-back.")
    last_nudge_at = fields.Datetime(readonly=True, copy=False)

    # The marketing desk's own verdict on the day, given at approval. Feeds the
    # weekly report and Team Performance with a quality dimension the raw counts
    # never had.
    marketing_score = fields.Selection(
        [('1', '1 - Poor'), ('2', '2'), ('3', '3 - Fair'),
         ('4', '4'), ('5', '5 - Excellent')],
        string='Activity Score', copy=False, tracking=True)
    marketing_remark = fields.Char('Marketing Remark', copy=False)

    summary_html = fields.Html(
        compute='_compute_summary_html', sanitize=False,
        help="The day as tiles: each figure against this executive's own "
             "recent average, plus the approval trail. Rendered from the "
             "snapshot fields, so it shows exactly what was signed.")
    streak = fields.Integer(compute='_compute_streak',
                            help="Consecutive earlier days approved clean.")
    timeline_html = fields.Html(compute='_compute_timeline', sanitize=False)
    can_ops_approve = fields.Boolean(compute='_compute_can')
    can_marketing_approve = fields.Boolean(compute='_compute_can')

    _user_day_uniq = models.Constraint(
        'UNIQUE(user_id, date, company_id)',
        'There is already a day sheet for this executive on this date.')

    # ---------------------------------------------------------------- helpers
    def _day_bounds_utc(self):
        """[start, end) of this sheet's date in the executive's own timezone."""
        self.ensure_one()
        return (local_midnight_utc(self.env, self.date, self.user_id),
                local_midnight_utc(self.env, self.date + timedelta(days=1),
                                   self.user_id))

    def _visits(self):
        self.ensure_one()
        return self.env['lab.visit'].sudo().search(
            [('user_id', '=', self.user_id.id), ('date', '=', self.date),
             ('state', '!=', 'cancel')], order='check_in, id')

    def _local_hm(self, value):
        if not value:
            return ''
        return fields.Datetime.context_timestamp(
            self.with_context(tz=tz_name(self.env, self.user_id)),
            value).strftime('%H:%M')

    # ---------------------------------------------------------------- facts
    def action_refresh(self):
        for sheet in self:
            if sheet.state not in ('draft', 'sent_back'):
                raise UserError(_(
                    "%s is already on an approver's desk - what they sign must "
                    "stay what they saw.", sheet.display_name))
        self._collect_facts()
        return True

    def _collect_facts(self):
        """Fill the sheet from what the day actually recorded. Sudo throughout:
        the sheet may be assembled by a cron, or read facts (deliveries,
        attendance) its owner cannot browse."""
        for sheet in self:
            visits = sheet._visits()
            done = visits.filtered(lambda v: v.state == 'done')
            open_visits = visits.filtered(lambda v: v.state in ('planned', 'open'))
            trips = self.env['lab.trip'].sudo().search(
                [('user_id', '=', sheet.user_id.id), ('date', '=', sheet.date),
                 ('state', '!=', 'cancel')])
            start, end = sheet._day_bounds_utc()

            vals = {
                'visit_count': len(visits),
                'visit_done_count': len(done),
                'gps_ok_count': len(done.filtered(lambda v: v.gps_state == 'ok')),
                'gps_far_count': len(done.filtered(
                    lambda v: v.gps_state in ('far', 'nofix'))),
                'gps_nopin_count': len(done.filtered(
                    lambda v: v.gps_state == 'nopin')),
                'minutes_at_clinics': sum(done.mapped('minutes')),
                'case_count': sum(done.mapped('case_count')),
                'cases_counted': sum(done.mapped('cases_counted')),
                'reworks_collected': sum(done.mapped('reworks_collected')),
                'order_count': sum(done.mapped('order_count')),
                'order_value': sum(done.mapped('order_value')),
                # Plus what was collected away from a visit: the sheet is the
                # day as it happened, and that money happened. (client, 2026-09-12)
                'collected': sum(done.mapped('collected')) + self.env[
                    'lab.cash.collection']._collected_for(
                        [sheet.user_id.id], sheet.date).get(sheet.user_id.id, 0.0),
                'km': sum(trips.mapped('distance')),
            }

            # Downstream modules, if they are there. lab_delivery depends on
            # this module, never the other way round, so it is felt for.
            if 'lab.delivery' in self.env:
                vals['delivery_count'] = self.env['lab.delivery'].sudo().search_count(
                    [('executive_id', '=', sheet.user_id.id),
                     ('state', '=', 'delivered'),
                     ('delivered_datetime', '>=', start),
                     ('delivered_datetime', '<', end)])
            # Top-level partners only: a doctor added under a clinic is a
            # contact, not a new door.
            vals['new_clinic_count'] = self.env['res.partner'].sudo().search_count(
                [('create_uid', '=', sheet.user_id.id), ('team_id', '!=', False),
                 ('parent_id', '=', False),
                 ('create_date', '>=', start), ('create_date', '<', end)])

            # Attendance: the day's first in and last out, said in the
            # executive's own clock.
            employee = sheet.user_id.sudo()._fw_employee() \
                if hasattr(sheet.user_id, '_fw_employee') else None
            att = self.env['hr.attendance'].sudo().search(
                [('employee_id', '=', employee.id),
                 ('check_in', '>=', start), ('check_in', '<', end)],
                order='check_in') if employee else self.env['hr.attendance'].sudo()
            vals['attendance_in'] = sheet._local_hm(att[:1].check_in)
            last = att[-1:]
            vals['attendance_out'] = sheet._local_hm(last.check_out)
            # hr.attendance has no auto_check_out field; the day closer stamps
            # Odoo's own out_mode, so that is what is read back.
            vals['attendance_auto_closed'] = bool(last) and \
                last.out_mode == 'auto_check_out'

            # ------------------------------------------------------ anomalies
            reasons = []
            # Deliberately NOT part of flag_gps, which means "went to the wrong
            # place". Not being able to check is a different fact, and folding it in
            # would mark every sheet in the lab as an exception until all 6,858
            # clinics carry a pin - which would empty Approve All Clean rather than
            # inform anybody. It is stated in words instead, so a reader is never
            # left to infer a pass from a zero.
            if vals['gps_nopin_count']:
                reasons.append(_(
                    "GPS could not be checked on %s visit(s): the clinic is not "
                    "pinned on the map.", vals['gps_nopin_count']))
            vals['flag_gps'] = vals['gps_far_count'] > 0
            if vals['flag_gps']:
                reasons.append(_("%s visit(s) checked in away from the clinic "
                                 "or with no GPS fix.", vals['gps_far_count']))
            vals['flag_open_work'] = bool(open_visits) or bool(
                visits.filtered('auto_close_failed')) or bool(
                trips.filtered(lambda t: t.state in ('draft', 'open')))
            if vals['flag_open_work']:
                reasons.append(_("Visits or travel were left open - an outcome "
                                 "or a closing odometer is missing."))
            vals['flag_zero_visits'] = not visits and bool(vals['attendance_in'])
            if vals['flag_zero_visits']:
                reasons.append(_("On duty, but no visit was recorded."))
            vals['flag_attendance'] = vals['attendance_auto_closed'] or (
                bool(vals['attendance_in']) and not vals['attendance_out'])
            if vals['flag_attendance']:
                reasons.append(_("The day was not signed out by the person - "
                                 "attendance closed itself or is still open."))
            vals['flag_km_outlier'] = sheet._km_is_outlier(vals['km'])
            if vals['flag_km_outlier']:
                reasons.append(_("%(km)s km is far above this executive's "
                                 "recent daily average.", km=round(vals['km'], 1)))
            vals['anomaly_note'] = '\n'.join(reasons)
            vals['is_clean'] = not any(
                vals[f] for f in ('flag_gps', 'flag_open_work', 'flag_zero_visits',
                                  'flag_km_outlier', 'flag_attendance'))
            # sudo: the snapshot is the system's, never the executive's to type.
            sheet.sudo().write(vals)
        return True

    def _km_is_outlier(self, km):
        """More than double the executive's own recent average - their round is
        their baseline, not some company-wide number."""
        self.ensure_one()
        if not km:
            return False
        recent = self.env['lab.trip'].sudo().search_read(
            [('user_id', '=', self.user_id.id),
             ('date', '>=', self.date - timedelta(days=30)),
             ('date', '<', self.date),
             ('state', 'in', ('closed', 'approved')),
             ('distance', '>', 0)],
            ['distance'], limit=60)
        if len(recent) < 3:
            return False
        avg = sum(r['distance'] for r in recent) / len(recent)
        return km > 2 * avg

    # ---------------------------------------------------------------- computes
    @api.depends('date')
    def _compute_date_label(self):
        for sheet in self:
            sheet.date_label = day_label(sheet.date)

    def _compute_display_name(self):
        for sheet in self:
            sheet.display_name = _('%(user)s — %(date)s',
                                   user=sheet.user_id.name or '?',
                                   date=day_label(sheet.date))

    def _compute_streak(self):
        for sheet in self:
            previous = self.search(
                [('user_id', '=', sheet.user_id.id), ('date', '<', sheet.date)],
                order='date desc', limit=STREAK_HORIZON)
            streak = 0
            for prior in previous:
                if prior.state == 'approved' and prior.is_clean:
                    streak += 1
                else:
                    break
            sheet.streak = streak

    @api.depends('state', 'submitted_at', 'ops_approved_by_id',
                 'approved_by_id', 'marketing_score', 'visit_count',
                 'visit_done_count', 'collected', 'km', 'order_count',
                 'order_value', 'gps_ok_count', 'gps_far_count')
    def _compute_summary_html(self):
        """Tiles, not labelled fields: the client asked every screen in this
        module to say numbers first. Deltas are against the executive's OWN
        trailing month - "4 visits" means nothing until you know whether
        their usual day is 3 or 8. (client, 2026-08-31)"""
        for sheet in self:
            past = self.sudo().search([
                ('user_id', '=', sheet.user_id.id),
                ('date', '<', sheet.date),
                ('date', '>=', sheet.date - timedelta(days=30))])
            n = len(past) or 1
            avg = {
                'visits': sum(past.mapped('visit_done_count')) / n,
                'collected': sum(past.mapped('collected')) / n,
                'km': sum(past.mapped('km')) / n,
                'orders': sum(past.mapped('order_count')) / n,
            }

            def money(amount):
                return format_amount(self.env, amount or 0.0,
                                     sheet.currency_id)

            def delta(value, usual, fmt=lambda v: '%.0f' % v):
                if not past:
                    return ''
                diff = value - usual
                if abs(diff) < max(usual * 0.15, 0.51):
                    return ('<div style="font-size:11px; color:#94a3b8;">'
                            '= their usual</div>')
                return ('<div style="font-size:11px; font-weight:bold; '
                        'color:%s;">%s %s vs their usual</div>' % (
                            '#0f9d58' if diff > 0 else '#dc2626',
                            '&#9650;' if diff > 0 else '&#9660;',
                            fmt(abs(diff))))

            def tile(value, label, extra=''):
                return ('<td style="background:#f6f8fb; border-radius:10px; '
                        'padding:8px 14px; text-align:center;">'
                        '<div style="font-size:20px; font-weight:800; '
                        'color:#10233a;">%s</div>'
                        '<div style="font-size:11px; color:#64748b; '
                        'text-transform:uppercase;">%s</div>%s</td>'
                        '<td style="width:8px;"></td>' % (value, label, extra))

            gps_extra = ('<div style="font-size:11px; color:#dc2626; '
                         'font-weight:bold;">%s away</div>'
                         % sheet.gps_far_count) if sheet.gps_far_count else ''
            tiles = ('<table style="border-collapse:separate; margin:2px 0 8px;">'
                     '<tr>%s</tr></table>' % (
                         tile('%s/%s' % (sheet.visit_done_count,
                                         sheet.visit_count),
                              _('visits'),
                              delta(sheet.visit_done_count, avg['visits']))
                         + tile(money(sheet.collected), _('collected'),
                                delta(sheet.collected, avg['collected'], money))
                         + tile(sheet.order_count, _('orders'),
                                delta(sheet.order_count, avg['orders']))
                         + tile(money(sheet.order_value), _('order value'))
                         + tile('%.0f km' % sheet.km, _('distance'),
                                delta(sheet.km, avg['km'],
                                      lambda v: '%.0f km' % v))
                         + tile('%s/%s' % (sheet.gps_ok_count,
                                           sheet.visit_done_count),
                                _('at the door'), gps_extra)))

            def chip(text, bg, fg):
                return ('<span style="display:inline-block; background:%s; '
                        'color:%s; border-radius:999px; padding:2px 11px; '
                        'font-size:12px; font-weight:bold; '
                        'margin:1px 6px 1px 0;">%s</span>' % (bg, fg, text))

            trail = []
            if sheet.submitted_at:
                trail.append(chip(_('submitted %s',
                                    sheet._local_hm(sheet.submitted_at)),
                                  '#e2e8f0', '#334155'))
            else:
                trail.append(chip(_('not submitted yet'), '#e2e8f0', '#64748b'))
            if sheet.ops_approved_by_id:
                trail.append(chip(_('Operations: %s',
                                    html_escape(sheet.ops_approved_by_id.name)),
                                  '#ccf5f0', '#0e5c56'))
            elif sheet.state == 'submitted':
                trail.append(chip(_('waiting for Operations'),
                                  '#fff3cd', '#8a6400'))
            if sheet.approved_by_id:
                trail.append(chip(_('Marketing: %s &#9733;%s',
                                    html_escape(sheet.approved_by_id.name),
                                    sheet.marketing_score or '-'),
                                  '#e9d5ff', '#5b21b6'))
            elif sheet.state in MARKETING_QUEUE:
                trail.append(chip(_('waiting for Marketing'),
                                  '#fff3cd', '#8a6400'))
            if sheet.ops_check_note:
                trail.append(chip(_('Operations checked - issues noted'),
                                  '#ffedd5', '#9a3412'))
            if sheet.state == 'checked':
                trail.append(chip(_('Marketing checked - issues noted'),
                                  '#ffedd5', '#9a3412'))
            if sheet.state == 'sent_back':
                trail.append(chip(_('sent back'), '#fee2e2', '#991b1b'))
            if sheet.streak:
                trail.append(chip(_('%s clean days in a row &#128293;',
                                    sheet.streak), '#fde68a', '#92400e'))
            sheet.summary_html = tiles + '<div>%s</div>' % ''.join(trail)

    def action_open_adjacent(self, direction=1):
        """The same executive's next or previous day sheet - an approver
        reads days in runs, and the breadcrumb round-trip was the tax on
        every single one."""
        self.ensure_one()
        op, order = ('>', 'date asc') if direction > 0 else ('<', 'date desc')
        neighbour = self.search([
            ('user_id', '=', self.user_id.id), ('date', op, self.date)],
            order=order, limit=1)
        if not neighbour:
            raise UserError(_("No %s day sheet for %s.",
                              _('later') if direction > 0 else _('earlier'),
                              self.user_id.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name, 'res_id': neighbour.id,
            'views': [(False, 'form')],
        }

    def action_open_prev_day(self):
        return self.action_open_adjacent(-1)

    def action_open_next_day(self):
        return self.action_open_adjacent(1)

    @api.depends_context('uid')
    def _compute_can(self):
        ops = self._may_sign('ops')
        marketing = self._may_sign('marketing')
        for sheet in self:
            sheet.can_ops_approve = ops and sheet.state == 'submitted'
            sheet.can_marketing_approve = marketing and sheet.state in MARKETING_QUEUE

    def _map_link(self, visit, end='start'):
        """A pin next to a visit that carries one — the approver's own eye.

        The GPS mark already says at-the-door / away / no fix, but "away"
        with no way to look was a verdict the reader could not check. The
        href is a plain maps query on the RECORDED coordinates (never the
        clinic's), so what opens is where the executive actually stood.
        `geo:` is right on a phone and useless on the desktop these sheets
        are read on, so this is https. (client, 2026-09-02)

        Both ends now: a visit carries a finish fix too, and the pair is the
        story — the same spot twice is a visit, two spots apart is a drive.
        (client, 2026-09-08)
        """
        if end == 'finish':
            lat, lon, what = visit.out_gps_lat, visit.out_gps_lon, _('finished')
        else:
            lat, lon, what = visit.gps_lat, visit.gps_lon, _('started')
        if not (lat and lon):
            return ''
        return (' <a href="https://www.google.com/maps?q=%s,%s" '
                'target="_blank" rel="noreferrer" '
                'style="color:#2563eb; text-decoration:none; font-size:12px;" '
                'title="%s">&#128205;<span style="font-size:10px;">%s</span></a>' % (
                    lat, lon,
                    _('Where this visit %(what)s: %(lat)s, %(lon)s',
                      what=what, lat=lat, lon=lon),
                    'S' if end == 'start' else 'F'))

    def _drift_text(self, visit):
        """How far the finish fix is from the start fix, when both exist.

        A visit whose two ends are a few hundred metres apart is a visit. One
        whose ends are kilometres apart was closed from somewhere else, and
        that is the fact an approver wants said in the line, not left for them
        to compute from two pins.
        """
        metres = metres_between(visit.gps_lat, visit.gps_lon,
                                visit.out_gps_lat, visit.out_gps_lon)
        if metres is None or metres < 200:
            return ''
        text = (_('moved %.1f km', metres / 1000.0) if metres >= 1000
                else _('moved %d m', int(metres)))
        return ' <span style="color:#dc3545;">· %s</span>' % html_escape(text)

    def _cases_text(self, visit):
        """The executive's hand counts - cases, then reworks - when given."""
        parts = []
        n = visit.cases_counted
        if n:
            parts.append(_('%s case', n) if n == 1 else _('%s cases', n))
        r = visit.reworks_collected
        if r:
            parts.append(_('%s rework', r) if r == 1 else _('%s reworks', r))
        return ''.join(' · ' + html_escape(p) for p in parts)

    def action_open_visit_map(self):
        """Every located visit of the day, on one list with its coordinates.

        The smart button says how many of the day's doors carry a fix; this
        is the list behind it, so "0/13 at the door" can be read rather
        than argued about."""
        self.ensure_one()
        return self._open(
            _('Where they were — %s', self.display_name), 'lab.visit',
            [('user_id', '=', self.user_id.id), ('date', '=', self.date),
             ('gps_lat', '!=', False), ('gps_lon', '!=', False)])

    def _compute_timeline(self):
        """The day as a walk, not a table: when it started, each door in order
        with how long and how sure the GPS was, when it ended. An approver reads
        this once instead of opening eight records."""
        icons = {'ok': ('&#10003;', '#0f9d58', _('at the door')),
                 'far': ('&#9888;', '#dc3545', _('away from the clinic')),
                 'nofix': ('?', '#d99b00', _('no GPS fix')),
                 # A fix was recorded; the clinic has no pin to measure it
                 # against. Distinct from 'nofix' on purpose - see _judge_fix.
                 'nopin': ('&#9679;', '#2563eb',
                           _('recorded, clinic not pinned'))}
        # Built once, translated: this was a dict rebuilt from the raw
        # selection for every visit of every sheet.
        outcome_labels = dict(self.env['lab.visit']._fields['outcome']
                              ._description_selection(self.env))
        for sheet in self:
            rows = []
            if sheet.attendance_in:
                rows.append(
                    '<div style="color:#6c757d;">%s &#8212; %s</div>'
                    % (sheet.attendance_in, _('started the day')))
            visits = sheet._visits()
            # Counted in the same pass that draws the rows: the drift used to
            # be measured once for the line and again for the summary.
            located = moved = 0
            for visit in visits:
                mark, colour, label = icons.get(visit.gps_state,
                                                ('&#8226;', '#6c757d', ''))
                drift = sheet._drift_text(visit)
                located += bool(visit.gps_state and visit.gps_state != 'nofix')
                moved += bool(drift)
                rows.append(
                    '<div><b>%s</b>&#8211;%s &nbsp;%s '
                    '<span style="color:%s;" title="%s">%s</span>%s'
                    '<span style="color:#6c757d;"> · %s%s%s</span></div>' % (
                        sheet._local_hm(visit.check_in) or '--:--',
                        sheet._local_hm(visit.check_out) or '--:--',
                        html_escape(visit.partner_id.display_name or ''),
                        colour, html_escape(label), mark,
                        sheet._map_link(visit) + sheet._map_link(visit, 'finish')
                        + drift,
                        html_escape(outcome_labels.get(visit.outcome, _('no outcome'))),
                        sheet._cases_text(visit),
                        (' · ' + format_amount(self.env, visit.collected,
                                               sheet.currency_id))
                        if visit.collected else ''))
            if sheet.attendance_out:
                rows.append('<div style="color:#6c757d;">%s &#8212; %s%s</div>' % (
                    sheet.attendance_out, _('ended the day'),
                    _(' (closed automatically)') if sheet.attendance_auto_closed
                    else ''))
            # Capped and scrolled INSIDE itself. A round of thirty clinics is
            # thirty lines, and stacked at line-height 1.9 that pushed the
            # notes, the figures and the approval trail off the bottom of the
            # screen - an approver had to scroll past the day to reach the
            # buttons that judge it. The rows are all still here; the page
            # around them stopped growing with the round. (client, 2026-09-05)
            count = len(visits)
            # The number the whole location feature exists to produce: how
            # many of the day's doors the phone actually placed. "0 of 32" is
            # a day typed at a desk; "32 of 32" is a round.
            summary = ''
            if count:
                summary = html_escape(_(
                    '%(located)s of %(count)s carry a location',
                    located=located, count=count))
                if moved:
                    summary += html_escape(_(
                        ' · %s finished away from where they started', moved))
            sheet.timeline_html = (
                '<div class="o_fw_tl_head">%s</div>'
                '<div class="o_fw_tl_sub">%s</div>'
                '<div class="o_fw_tl" style="line-height:1.55;">%s</div>' % (
                    html_escape(_('%s visits, in the order they happened', count))
                    if count else '',
                    summary,
                    ''.join(rows))
                if rows else False)

    # ---------------------------------------------------------------- lifecycle
    def _may_write_freely(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    @api.model_create_multi
    def create(self, vals_list):
        # The same line as write(): an executive starts a sheet for a day and
        # may say something about it, and nothing else. A sheet created
        # already 'approved' with a score was the create-side twin of the
        # write hole. (2026-09-15)
        if not self._may_write_freely():
            own = {'user_id', 'date', 'company_id', 'note'}
            for vals in vals_list:
                extra = [f for f, v in vals.items()
                         if f not in own and v and not (f == 'state' and v == 'draft')]
                if extra:
                    raise UserError(_(
                        "A day sheet assembles itself from the day's records; "
                        "only the note is yours to write."))
        sheets = super().create(vals_list)
        sheets._collect_facts()
        return sheets

    def write(self, vals):
        """The executive writes the note, and only while the sheet is theirs.

        The access list has to grant an executive write on their own sheet
        for the note, and a write right is a write right over RPC: the state,
        both signatures, the score and every snapshot figure were theirs to
        set, so a day could approve itself. Everything else now moves through
        the action methods, which check who is signing and write elevated.
        A stand-in on a desk may still type what an approver types before
        pressing a button. (2026-09-15)
        """
        if not self._may_write_freely():
            fields_ = set(vals)
            typed = fields_ & APPROVER_INPUT_FIELDS
            if typed and (self._may_sign('ops') or self._may_sign('marketing')):
                fields_ -= typed
            if fields_ - {'note'}:
                raise UserError(_(
                    "A day sheet assembles itself from the day's records and is "
                    "signed by the desks; only the note is yours to write."))
            if fields_:
                closed = self.filtered(
                    lambda s: s.state not in ('draft', 'sent_back'))
                if closed:
                    raise UserError(_(
                        "%s is already on an approver's desk - the note can be "
                        "changed while it is a draft or after it is sent back.",
                        closed[0].display_name))
        return super().write(vals)

    def action_submit(self):
        for sheet in self:
            if sheet.state not in ('draft', 'sent_back'):
                raise UserError(_("%s is already submitted.", sheet.display_name))
            if sheet.user_id != self.env.user and not self.env.user.has_group(
                    'lab_fieldwork.group_fieldwork_manager'):
                raise UserError(_("A day sheet is submitted by the person whose "
                                  "day it was."))
        self._collect_facts()   # what goes to the desk is the day as of NOW
        self.sudo().write({'state': 'submitted',
                           'submitted_at': fields.Datetime.now(),
                           'send_back_reason': False})
        for sheet in self:
            sheet.message_post(body=_("Day sheet submitted."))
        return True

    def _may_sign(self, desk):
        """Holds the desk, or is standing in on it today.

        One question asked in one place, so a cover can never drift out of step
        with the guard it exists to satisfy. See lab.desk.cover.
        """
        group = {'ops': 'lab_fieldwork.group_fieldwork_ops_manager',
                 'marketing': 'lab_fieldwork.group_fieldwork_marketing_manager'}
        return self.env.user.has_group(group[desk]) or \
            self.env['lab.desk.cover']._covers(self.env.user, desk)

    def action_ops_approve(self):
        if not self._may_sign('ops'):
            raise UserError(_("Only an Operational Manager signs the first "
                              "approval - it vouches that the day physically "
                              "happened as recorded."))
        for sheet in self:
            if sheet.state != 'submitted':
                raise UserError(_("%s is not waiting for operational approval.",
                                  sheet.display_name))
        # sudo: a stand-in signs without holding the group the write guard asks
        # for. The signature is still self.env.uid - the person who pressed it.
        self.sudo().write({'state': 'ops_approved',
                           'ops_approved_by_id': self.env.uid,
                           'ops_approved_at': fields.Datetime.now()})
        for sheet in self:
            sheet.message_post(body=_("Operations approved - passed to the "
                                      "marketing desk."))
        return True

    def action_marketing_approve(self):
        if not self._may_sign('marketing'):
            raise UserError(_("Only a Marketing Manager signs the second "
                              "approval - it judges the day's activities."))
        for sheet in self:
            if sheet.state not in MARKETING_QUEUE:
                # The order is the design: activities are judged only on days
                # operations has vouched for.
                raise UserError(_(
                    "%s has not been operationally approved yet. The marketing "
                    "approval always comes second.", sheet.display_name))
        self.sudo().write({'state': 'approved',
                           'approved_by_id': self.env.uid,
                           'approved_at': fields.Datetime.now()})
        for sheet in self:
            sheet.message_post(body=_("Activities approved."))
        return True

    def action_ops_unapprove(self):
        """Take back the operational approval while marketing still has not
        signed.

        An approver who signs the wrong row (or spots the missing odometer a
        minute later) had no way back: the sheet sat on the marketing desk
        until somebody sent it all the way back to the executive with a
        reason, which blames the executive for the approver\'s slip. This
        returns it to the operational desk and says who took it back, in the
        chatter. Once marketing has approved, the day is judged and this
        refuses — that is the "before the next level approves" line.
        (client, 2026-09-02)
        """
        if not self._may_sign('ops'):
            raise UserError(_("Only an Operational Manager can take back the "
                              "operational approval."))
        for sheet in self:
            if sheet.state in SIGNED:
                raise UserError(_(
                    "%s has already been approved by the marketing desk. Send "
                    "it back with a reason instead.", sheet.display_name))
            if sheet.state not in MARKETING_QUEUE:
                raise UserError(_("%s is not operationally approved.",
                                  sheet.display_name))
        self.sudo().write({'state': 'submitted', 'ops_approved_by_id': False,
                           'ops_approved_at': False, 'ops_check_note': False})
        for sheet in self:
            sheet.message_post(body=_(
                "Operational approval withdrawn by %s - back on the "
                "operational desk.", self.env.user.name))
        return True

    def action_ops_check(self, note=None):
        """Checked by Operations, not approved: the day has issues.

        Approving a day with issues made it look exactly like a clean day
        signed, so the marketing desk and the administrator could not tell
        anyone had read it. The day still moves on to the marketing desk; the
        note says what was checked. (client, 2026-09-15)
        """
        if not self._may_sign('ops'):
            raise UserError(_("Only an Operational Manager checks a day on the "
                              "operational desk."))
        for sheet in self:
            if sheet.state != 'submitted':
                raise UserError(_("%s is not waiting for operational approval.",
                                  sheet.display_name))
        for sheet in self:
            # No note required: pressing it is the check. A note typed on the
            # form is kept; otherwise the day still says it was checked.
            # (client, 2026-09-15)
            text = (note or sheet.ops_check_note or '').strip() \
                or _("Checked, issues noted")
            sheet.sudo().write({'state': 'ops_checked', 'ops_check_note': text,
                                'ops_approved_by_id': self.env.uid,
                                'ops_approved_at': fields.Datetime.now()})
            sheet.message_post(body=_(
                "Checked by Operations, issues noted: %s", text))
        return True

    def action_marketing_check(self, note=None):
        """Checked by Marketing, not approved: the day has issues."""
        if not self._may_sign('marketing'):
            raise UserError(_("Only a Marketing Manager checks a day's "
                              "activities."))
        for sheet in self:
            if sheet.state not in MARKETING_QUEUE:
                raise UserError(_(
                    "%s has not been through the operational desk yet. The "
                    "marketing desk always comes second.", sheet.display_name))
        for sheet in self:
            text = (note or sheet.check_note or '').strip() \
                or _("Checked, issues noted")
            sheet.sudo().write({'state': 'checked', 'check_note': text,
                                'approved_by_id': self.env.uid,
                                'approved_at': fields.Datetime.now()})
            sheet.message_post(body=_(
                "Checked by Marketing, issues noted: %s", text))
        return True

    # ------------------------------------------------------- raised upstairs
    FLAG_WINDOW_DAYS = 90

    @api.depends('user_id', 'date', 'flagged_at')
    def _compute_flag_history(self):
        """How many times this executive has been raised in the window.

        Counted over the sheets themselves rather than a running total on the
        user, so it stays true when a sheet is deleted or a flag withdrawn.
        """
        window = fields.Date.subtract(fields.Date.context_today(self),
                                      days=self.FLAG_WINDOW_DAYS)
        by_user = {}
        users = self.mapped('user_id')
        if users:
            rows = self.sudo()._read_group(
                [('user_id', 'in', users.ids), ('date', '>=', window),
                 ('flagged_at', '!=', False)],
                ['user_id'], ['__count'])
            by_user = {user.id: count for user, count in rows}
        for sheet in self:
            sheet.flag_history_count = by_user.get(sheet.user_id.id, 0)

    def _suggested_flag_kind(self):
        """The kind this sheet's own numbers point at, offered as the default.

        The desk is reading a screen that has already noticed something - no
        GPS on any visit, a round with no kilometres, times that land in one
        clump - so making them classify it again from scratch is asking twice.
        They can always change it.
        """
        self.ensure_one()
        if self.visit_done_count and not self.gps_ok_count:
            return 'location'
        if self.visit_done_count > 3 and not self.km:
            return 'travel'
        if self.attendance_auto_closed or not self.attendance_out:
            return 'attendance'
        if self.collected and not self.order_count:
            return 'money'
        return 'other'

    def action_open_flag(self):
        """Open the raise-it dialog, with the kind already guessed."""
        self.ensure_one()
        if self.state not in ON_A_DESK:
            raise UserError(_("%s is not on an approver's desk.",
                              self.display_name))
        if not self.flag_kind:
            self.sudo().flag_kind = self._suggested_flag_kind()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Raise to the Administrator'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'lab_fieldwork.view_daily_update_flag_form').id, 'form')],
            'target': 'new',
        }

    def action_flag(self):
        """Send this day UP, not back: the executive is not the audience.

        Send Back returns the sheet to the person who wrote it and clears the
        approval trail. This keeps the day where it is, records who raised it
        and what kind of problem it is, and puts it on the administrator's
        desk. (client, 2026-09-05)
        """
        for sheet in self:
            if sheet.state not in ON_A_DESK:
                raise UserError(_("%s is not on an approver's desk.",
                                  sheet.display_name))
            desk = 'ops' if sheet.state == 'submitted' else 'marketing'
            if not self._may_sign(desk):
                raise UserError(_("This sheet is on the %s desk.", desk))
            if not (sheet.flag_reason or '').strip():
                raise UserError(_(
                    "Say what you saw. A flag with no reason is a sheet the "
                    "administrator has to investigate from nothing."))
            if not sheet.flag_kind:
                raise UserError(_(
                    "Choose what kind of problem this is, so it can be counted "
                    "with the others like it."))
            sheet.sudo().write({
                'state': 'flagged', 'flagged_desk': desk,
                'flagged_by_id': self.env.uid,
                'flagged_at': fields.Datetime.now(),
                'flag_answer': False, 'flag_closed_by_id': False,
                'flag_closed_at': False,
            })
            sheet.message_post(body=_(
                "Raised to the administrator (%(kind)s): %(why)s",
                kind=dict(FLAG_KINDS).get(sheet.flag_kind, ''),
                why=sheet.flag_reason))
            # The administrator is told, rather than being expected to notice.
            admins = self.env['res.users'].sudo().search(
                [('group_ids', 'in', self.env.ref(
                    'lab_fieldwork.group_fieldwork_admin').id)])
            if admins:
                sheet.message_notify(
                    partner_ids=admins.partner_id.ids,
                    body=_("%(who)s — %(date)s was raised by the %(desk)s desk: "
                           "%(why)s",
                           who=sheet.user_id.name, date=day_label(sheet.date),
                           desk=desk, why=sheet.flag_reason),
                    subject=_("Day sheet raised to you"))
        return True

    def _check_admin(self):
        if not (self.env.su or self.env.user.has_group(
                'lab_fieldwork.group_fieldwork_admin')):
            raise UserError(_(
                "Only the Field Work Administrator answers a raised sheet - "
                "that is the point of raising it."))

    def action_flag_return(self):
        """Answered: back to the desk that raised it, to sign or send back."""
        self._check_admin()
        for sheet in self:
            if sheet.state != 'flagged':
                raise UserError(_("%s was not raised.", sheet.display_name))
            if not (sheet.flag_answer or '').strip():
                raise UserError(_(
                    "Write what you decided. A sheet that comes back without "
                    "an answer arrives on the same desk with the same doubt."))
            back = 'submitted' if sheet.flagged_desk == 'ops' else (
                'ops_checked' if sheet.ops_check_note else 'ops_approved')
            sheet.write({'state': back,
                         'flag_closed_by_id': self.env.uid,
                         'flag_closed_at': fields.Datetime.now()})
            sheet.message_post(body=_("Administrator answered: %s",
                                      sheet.flag_answer))
            if sheet.flagged_by_id:
                sheet.message_notify(
                    partner_ids=sheet.flagged_by_id.partner_id.ids,
                    body=_("%(who)s — %(date)s is back on your desk: %(ans)s",
                           who=sheet.user_id.name, date=day_label(sheet.date),
                           ans=sheet.flag_answer),
                    subject=_("Raised sheet answered"))
        return True

    def action_flag_to_executive(self):
        """The administrator agrees with the desk: it goes to the executive.

        Routed through the ordinary send-back so the executive's notification,
        the cleared approval trail and the reason all behave identically - a
        second way of doing the same thing would drift from the first.
        """
        self._check_admin()
        for sheet in self:
            if sheet.state != 'flagged':
                raise UserError(_("%s was not raised.", sheet.display_name))
            if not (sheet.send_back_reason or '').strip():
                sheet.send_back_reason = sheet.flag_answer or sheet.flag_reason
            sheet.write({'state': sheet.flagged_desk == 'ops'
                         and 'submitted'
                         or (sheet.ops_check_note and 'ops_checked' or 'ops_approved'),
                         'flag_closed_by_id': self.env.uid,
                         'flag_closed_at': fields.Datetime.now()})
            sheet.action_send_back()
        return True

    def action_send_back(self):
        for sheet in self:
            if sheet.state not in ON_A_DESK:
                raise UserError(_("%s is not on an approver's desk.",
                                  sheet.display_name))
            if not (sheet.send_back_reason or '').strip():
                raise UserError(_(
                    "Say what is wrong. A sheet that comes back with no reason "
                    "just comes back again the same."))
            # The administrator is the other end of the escalation, so they
            # can send back too - action_flag_to_executive comes through here
            # on purpose, rather than growing a second send-back that would
            # drift from this one. (client, 2026-09-05)
            boss = self.env.user.has_group(
                'lab_fieldwork.group_fieldwork_admin')
            if sheet.state == 'submitted' and not (boss or self._may_sign('ops')):
                raise UserError(_("This sheet is on the operational desk."))
            if sheet.state in MARKETING_QUEUE \
                    and not (boss or self._may_sign('marketing')):
                raise UserError(_("This sheet is on the marketing desk."))
            sheet.sudo().write({'state': 'sent_back',
                                'ops_approved_by_id': False,
                                'ops_approved_at': False,
                                'ops_check_note': False})
            sheet.message_post(body=_("Sent back: %s", sheet.send_back_reason))
            # In their inbox, not only in a chatter they never open.
            sheet.message_notify(
                partner_ids=sheet.user_id.partner_id.ids,
                body=_("Your day sheet for %(date)s was sent back: %(why)s",
                       date=day_label(sheet.date), why=sheet.send_back_reason),
                subject=_("Day sheet sent back"))
        return True

    # ------------------------------------------------------------ verification
    # The smart buttons: every figure on the sheet is a SNAPSHOT, and these
    # open the live records behind it - which is exactly how an approver
    # verifies a number they doubt. If the button's count and the list under
    # it disagree, a record moved after submission, and that is worth seeing.
    def _open(self, name, model, domain, view_mode='list,form'):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': view_mode,
            'domain': domain,
            'context': {'create': False},
        }

    # Live counts for the buttons whose records are not snapshot figures on
    # the sheet. Not stored, on purpose: a button's number must match the list
    # it opens, and these are read for one record on one form.
    dispatch_count = fields.Integer(compute='_compute_button_counts')
    # What is STILL riding with this executive right now — the question an
    # approver actually has at sign-off. The dispatch count is what the day
    # touched; this is what never came back. (client, 2026-09-02)
    undelivered_count = fields.Integer(compute='_compute_button_counts')
    located_visit_count = fields.Integer(compute='_compute_button_counts')
    cash_visit_count = fields.Integer(compute='_compute_button_counts')
    attendance_count = fields.Integer(compute='_compute_button_counts')

    def _compute_button_counts(self):
        for sheet in self:
            start, end = sheet._day_bounds_utc()
            sheet.dispatch_count = (
                self.env['lab.delivery'].sudo().search_count(
                    sheet._dispatch_domain(start, end))
                if 'lab.delivery' in self.env else 0)
            sheet.undelivered_count = (
                self.env['lab.delivery'].sudo().search_count(
                    sheet._undelivered_domain())
                if 'lab.delivery' in self.env else 0)
            sheet.located_visit_count = self.env['lab.visit'].sudo() \
                .search_count([('user_id', '=', sheet.user_id.id),
                               ('date', '=', sheet.date),
                               ('gps_lat', '!=', False),
                               ('gps_lon', '!=', False)])
            sheet.cash_visit_count = self.env['lab.visit'].sudo().search_count(
                [('user_id', '=', sheet.user_id.id), ('date', '=', sheet.date),
                 ('collected', '>', 0)])
            employee = sheet.user_id.sudo()._fw_employee() \
                if hasattr(sheet.user_id, '_fw_employee') else None
            sheet.attendance_count = self.env['hr.attendance'].sudo().search_count(
                [('employee_id', '=', employee.id),
                 ('check_in', '>=', start), ('check_in', '<', end)]) \
                if employee else 0

    def _dispatch_domain(self, start, end):
        """The work this executive carried that day: what went out with them
        and what they handed over, whichever timestamp the day touched."""
        return ['&', ('executive_id', '=', self.user_id.id),
                '|',
                '&', ('out_datetime', '>=', start), ('out_datetime', '<', end),
                '&', ('delivered_datetime', '>=', start),
                     ('delivered_datetime', '<', end)]

    def _undelivered_domain(self):
        """Dispatches this executive is carrying that have not been handed
        over — no date bound: a case that went out three weeks ago and never
        arrived is exactly the one worth surfacing.

        Bounded to the LIVE set: every dispatch row on this database is draft
        and 97.9% of them ride on orders that already shipped, so an unfiltered
        count would put four figures of finished paperwork on a day sheet.
        (2026-09-02)"""
        Delivery = self.env['lab.delivery'].sudo()
        return [('executive_id', '=', self.user_id.id),
                ('id', 'in', Delivery._live_ids())]

    def action_open_undelivered(self):
        self.ensure_one()
        return self._open(
            _('Still undelivered — %s', self.user_id.name), 'lab.delivery',
            self._undelivered_domain())

    def action_open_visits(self):
        return self._open(
            _('Visits — %s', self.display_name), 'lab.visit',
            [('user_id', '=', self.user_id.id), ('date', '=', self.date)])

    def action_open_trips(self):
        return self._open(
            _('Travel — %s', self.display_name), 'lab.trip',
            [('user_id', '=', self.user_id.id), ('date', '=', self.date)])

    def action_open_orders(self):
        visits = self._visits()
        return self._open(
            _('Orders — %s', self.display_name), 'sale.order',
            [('visit_id', 'in', visits.ids)])

    def action_open_dispatches(self):
        self.ensure_one()
        start, end = self._day_bounds_utc()
        return self._open(
            _('Dispatches — %s', self.display_name), 'lab.delivery',
            self._dispatch_domain(start, end))

    def action_open_cases(self):
        return self._open(
            _('Case slips — %s', self.display_name), 'lab.case',
            [('user_id', '=', self.user_id.id), ('date', '=', self.date)])

    def action_open_cash_visits(self):
        """The money, visit by visit: which doors the collected total came
        from, with each visit's own amount and pay mode."""
        return self._open(
            _('Cash collected — %s', self.display_name), 'lab.visit',
            [('user_id', '=', self.user_id.id), ('date', '=', self.date),
             ('collected', '>', 0)])

    def action_open_attendance(self):
        self.ensure_one()
        start, end = self._day_bounds_utc()
        employee = self.user_id.sudo()._fw_employee() \
            if hasattr(self.user_id, '_fw_employee') else None
        return self._open(
            _('Attendance — %s', self.display_name), 'hr.attendance',
            [('employee_id', '=', employee.id if employee else 0),
             ('check_in', '>=', start), ('check_in', '<', end)])

    def action_open_new_clinics(self):
        self.ensure_one()
        start, end = self._day_bounds_utc()
        return self._open(
            _('New clinics — %s', self.display_name), 'res.partner',
            [('create_uid', '=', self.user_id.id), ('team_id', '!=', False),
             ('parent_id', '=', False),
             ('create_date', '>=', start), ('create_date', '<', end)])

    def action_nudge(self, message=None):
        """Ask a question without rejecting the day.

        Send-back is a blunt instrument: it wipes the operational signature,
        pushes the sheet out of the queue and costs the executive a resubmission
        — for something that is often one missing odometer photo. Managers were
        therefore doing it by WhatsApp, off the record, and the sheet sat in the
        queue looking untouched while the conversation happened somewhere the
        next approver could not see it.

        A nudge leaves the sheet exactly where it is, on the desk, and puts the
        question in the executive's inbox and the sheet's own chatter. The count
        is the useful part: a sheet nudged twice with nothing changed is a
        send-back that should have happened. (client, 2026-08-31)
        """
        for sheet in self:
            if sheet.state not in ON_A_DESK:
                raise UserError(_("%s is not on an approver's desk.",
                                  sheet.display_name))
        text = (message or '').strip() or _(
            "Please take another look at this day sheet.")
        for sheet in self:
            sheet.sudo().write({'nudge_count': sheet.nudge_count + 1,
                                'last_nudge_at': fields.Datetime.now()})
            sheet.message_post(body=_("Nudged: %s", text))
            sheet.message_notify(
                partner_ids=sheet.user_id.partner_id.ids,
                subject=_("A question about your day sheet"),
                body=_("%(who)s asked about your day sheet for %(date)s: "
                       "%(what)s", who=self.env.user.name,
                       date=day_label(sheet.date),
                       what=text))
        return True

    def action_approve_clean(self):
        """One tap for the days that need no reading: every check green, nothing
        flagged. The exceptions stay on the desk, which is the whole point -
        approval time goes where the problems are."""
        clean = self.filtered(lambda s: s.state == 'submitted' and s.is_clean)
        if clean:
            clean.action_ops_approve()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'success' if clean else 'warning',
                'title': _("Clean days approved"),
                'message': _(
                    "%(done)s clean day(s) approved. %(left)s exception(s) "
                    "left for reading.",
                    done=len(clean),
                    left=len(self.filtered(lambda s: s.state == 'submitted'))),
                'sticky': False,
            },
        }

    def unlink(self):
        if self.filtered(lambda s: s.state in MARKETING_QUEUE + SIGNED):
            raise UserError(_("An approved day sheet is a signed document - it "
                              "is not deleted."))
        return super().unlink()

    # ---------------------------------------------------------------- crons
    @api.model
    def _cron_generate_daily_updates(self):
        """Hourly, like the day-closer, and for the same reason: "the day is
        over" is true at a different UTC hour for every timezone in the team.
        For each executive whose evening cutoff has passed, today's sheet is
        assembled; yesterday's is always caught up in case a run was missed.
        Policy permitting, a freshly assembled sheet goes straight onto the
        operational desk - nobody's evening ends with paperwork."""
        icp = self.env['ir.config_parameter'].sudo()
        auto_submit = icp.get_param(
            'lab_fieldwork.auto_submit_day_sheet', 'True') in (
            'True', 'true', '1')
        closer = self.env['lab.day.closer']
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        if not group:
            return 0
        made = 0
        for user in group.sudo().all_user_ids.filtered('active'):
            user_today = fields.Date.context_today(
                self.with_context(tz=tz_name(self.env, user)))
            days = [user_today - timedelta(days=1)]
            if closer._day_is_over_for(user):
                days.append(user_today)
            for day in days:
                made += 1 if self._ensure_sheet(user, day, auto_submit) else 0
        if made:
            _logger.info("day sheets: %s generated", made)
        return made

    @api.model
    def _ensure_sheet(self, user, day, auto_submit):
        """One executive, one day: create if they worked, refresh while still
        editable, submit if the policy says so. Returns whether one was created."""
        worked = self.env['lab.visit'].sudo().search_count(
            [('user_id', '=', user.id), ('date', '=', day)]) or \
            self.env['lab.trip'].sudo().search_count(
                [('user_id', '=', user.id), ('date', '=', day)])
        if not worked:
            return False
        company = user.company_id or self.env.company
        sheet = self.sudo().search(
            [('user_id', '=', user.id), ('date', '=', day),
             ('company_id', '=', company.id)], limit=1)
        created = not sheet
        if created:
            sheet = self.sudo().create(
                {'user_id': user.id, 'date': day, 'company_id': company.id})
        if sheet.state == 'draft':
            sheet._collect_facts()
            if auto_submit:
                sheet.write({'state': 'submitted',
                             'submitted_at': fields.Datetime.now()})
                sheet.message_post(body=_(
                    "Assembled and submitted automatically at day close."))
        return created

    @api.model
    def _sla_days(self):
        try:
            return max(1, int(float(self.env['ir.config_parameter'].sudo()
                                    .get_param('lab_fieldwork.approval_sla_days',
                                               2))))
        except (TypeError, ValueError):
            return 2

    @api.model
    def _cron_escalate_stale(self):
        """A sheet nobody signs is a decision nobody made. Past the SLA it is
        raised with the administrators - once, not every morning."""
        cutoff = fields.Datetime.now() - timedelta(days=self._sla_days())
        stale = self.sudo().search(
            ['|',
             '&', ('state', '=', 'submitted'), ('submitted_at', '<', cutoff),
             '&', ('state', 'in', MARKETING_QUEUE), ('ops_approved_at', '<', cutoff),
             ('escalated', '=', False)])
        if not stale:
            return 0
        admins = self.env.ref('lab_fieldwork.group_fieldwork_admin',
                              raise_if_not_found=False)
        partners = admins.sudo().all_user_ids.filtered('active').partner_id \
            if admins else self.env['res.partner']
        for sheet in stale:
            desk = _('operational') if sheet.state == 'submitted' else _('marketing')
            if partners:
                sheet.message_notify(
                    partner_ids=partners.ids,
                    body=_("%(sheet)s has waited on the %(desk)s desk for more "
                           "than %(days)s day(s).",
                           sheet=sheet.display_name, desk=desk,
                           days=self._sla_days()),
                    subject=_("Day sheet approval overdue"))
        stale.write({'escalated': True})
        _logger.info("day sheets: escalated %s stale approval(s)", len(stale))
        return len(stale)

    @api.model
    def _cron_morning_digest(self):
        """Each desk's queue, in its owner's inbox before the day starts. Sent
        only when there is something on it - an empty reminder teaches people to
        delete reminders."""
        counts = {
            'lab_fieldwork.group_fieldwork_ops_manager':
                (self.sudo().search_count([('state', '=', 'submitted')]),
                 _("day sheet(s) waiting for operational approval")),
            'lab_fieldwork.group_fieldwork_marketing_manager':
                (self.sudo().search_count([('state', 'in', MARKETING_QUEUE)]),
                 _("day(s) of activities waiting for marketing approval")),
        }
        sent = 0
        for xmlid, (count, what) in counts.items():
            if not count:
                continue
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                continue
            # Direct holders: the administrator implies both desks and would
            # otherwise be nagged twice for queues that are not theirs to clear.
            users = group.sudo().user_ids.filtered('active')
            for user in users:
                self.env['mail.thread'].message_notify(
                    partner_ids=user.partner_id.ids,
                    body=_("Good morning - %(count)s %(what)s.",
                           count=count, what=what),
                    subject=_("Field Work approvals waiting"))
                sent += 1
        return sent

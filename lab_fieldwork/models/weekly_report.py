# -*- coding: utf-8 -*-
"""The week, signed and filed with the administrator.

Every Monday the previous week is assembled into one stored record: what each
executive did, what each desk approved, what came back and why. It goes to the
Field Work Administrators by notification and mail - the two managers now share
what used to be one job, and the administrator is the one person positioned to
see whether the SPLIT is working: are sheets moving across both desks, or piling
up on one of them?

Stored rather than only mailed, on purpose: a report that exists only in
somebody's inbox cannot be pulled up in a review three months later.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount, html_escape

from .local_day import local_midnight_utc

_logger = logging.getLogger(__name__)


class LabWeeklyReport(models.Model):
    _name = 'lab.weekly.report'
    _description = 'Field Work Weekly Report'
    _order = 'date_from desc'
    _inherit = ['mail.thread']

    name = fields.Char(readonly=True)
    is_interim = fields.Boolean(
        readonly=True,
        help="Filed by hand mid-week from the Command Center. The Monday cron "
             "still files the real week; an interim is a snapshot, not the "
             "record.")
    date_from = fields.Date(readonly=True, index=True)
    date_to = fields.Date(readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    sheet_total = fields.Integer('Day Sheets', readonly=True)
    sheet_approved = fields.Integer('Fully Approved', readonly=True)
    sheet_ops_pending = fields.Integer('On the Ops Desk', readonly=True)
    sheet_mkt_pending = fields.Integer('On the Marketing Desk', readonly=True)
    sheet_sent_back = fields.Integer('Sent Back', readonly=True)
    clean_days = fields.Integer('Clean Days', readonly=True)
    visits_total = fields.Integer('Visits', readonly=True)
    order_value = fields.Monetary(readonly=True, currency_field='currency_id')
    collected = fields.Monetary(readonly=True, currency_field='currency_id')
    km_total = fields.Float('Distance (km)', readonly=True)
    new_clinics = fields.Integer('New Clinics', readonly=True)
    avg_marketing_score = fields.Float('Avg Activity Score', readonly=True,
                                       digits=(3, 1))

    # ------------------------------------------------ the week, interpreted
    # Stored at build time like everything else on this record: a report is a
    # snapshot, and its judgements must not drift after it is filed.
    approved_pct = fields.Float('Approved %', readonly=True, digits=(5, 1))
    clean_pct = fields.Float('Clean %', readonly=True, digits=(5, 1))
    delta_visits = fields.Integer('Visits vs Last Week', readonly=True)
    delta_collected = fields.Monetary('Collected vs Last Week', readonly=True,
                                      currency_field='currency_id')
    star_exec = fields.Char('Executive of the Week', readonly=True,
                            help="Most collected this week.")
    improved_exec = fields.Char('Most Improved', readonly=True,
                                help="Biggest jump in visits over their own "
                                     "previous week.")
    exceptions_total = fields.Integer('Days With Issues', readonly=True)
    ops_median_h = fields.Float('Ops Desk Median (h)', readonly=True,
                                digits=(6, 1))
    mkt_median_h = fields.Float('Marketing Desk Median (h)', readonly=True,
                                digits=(6, 1))
    insights_html = fields.Html(
        'Analysis', readonly=True, sanitize=False,
        help="What the week says about performance - sales by person and "
             "route, strike rates, coverage, and what to fix next week. "
             "Stored at filing time like everything else.")
    summary_text = fields.Text(
        'Share as Text', readonly=True,
        help="The whole report as plain text - select, copy, paste into "
             "WhatsApp or anywhere else.")

    body_html = fields.Html('Report', readonly=True, sanitize=False)

    # One REAL report per week; the interim lives beside it, not instead of
    # it. Without is_interim in the key, a mid-week snapshot for the current
    # week would make Monday's cron crash on this constraint instead of
    # filing the real report - found on the live database, where the client
    # had filed one. (2026-08-31)
    _week_uniq = models.Constraint(
        'UNIQUE(date_from, company_id, is_interim)',
        'This week has already been reported.')

    # ------------------------------------------------------------------ build
    @api.model
    def _build_week(self, date_from, date_to, company):
        sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', date_from), ('date', '<=', date_to),
             ('company_id', '=', company.id)], order='user_id, date')
        scored = sheets.filtered('marketing_score')
        vals = {
            'name': _('Field Work — week of %(start)s to %(end)s',
                      start=date_from, end=date_to),
            'date_from': date_from, 'date_to': date_to,
            'company_id': company.id,
            'sheet_total': len(sheets),
            # Signed off either way: approved, or checked with issues noted.
            'sheet_approved': len(sheets.filtered(
                lambda s: s.state in ('approved', 'checked'))),
            'sheet_ops_pending': len(sheets.filtered(
                lambda s: s.state == 'submitted')),
            'sheet_mkt_pending': len(sheets.filtered(
                lambda s: s.state in ('ops_approved', 'ops_checked'))),
            'sheet_sent_back': len(sheets.filtered(
                lambda s: s.state == 'sent_back')),
            'clean_days': len(sheets.filtered('is_clean')),
            'visits_total': sum(sheets.mapped('visit_done_count')),
            'order_value': sum(sheets.mapped('order_value')),
            'collected': sum(sheets.mapped('collected')),
            'km_total': sum(sheets.mapped('km')),
            'new_clinics': sum(sheets.mapped('new_clinic_count')),
            'avg_marketing_score': (
                sum(int(s.marketing_score) for s in scored) / len(scored)
                if scored else 0.0),
        }
        vals.update(self._interpret_week(vals, sheets, date_from, date_to,
                                         company))
        sales = self._sales_week(date_from, date_to, company)
        vals['body_html'] = self._render_body(vals, sheets, sales)
        vals['insights_html'] = self._render_insights(vals, sheets, sales)
        vals['summary_text'] = self._render_text(vals, sheets, sales)
        return vals

    @api.model
    def _sales_week(self, date_from, date_to, company):
        """The week's SALES, not just its field work: who sold, on which
        route, at which doors - this week against last. The day sheets carry
        the walking; the orders carry the business."""
        Order = self.env['sale.order'].sudo()

        def window(a, b):
            return [('state', '=', 'sale'), ('company_id', '=', company.id),
                    ('date_order', '>=', fields.Datetime.to_datetime(a)),
                    ('date_order', '<',
                     fields.Datetime.to_datetime(b + timedelta(days=1)))]

        this_week = window(date_from, date_to)
        last_week = window(date_from - timedelta(days=7),
                           date_from - timedelta(days=1))

        def grouped(domain, by):
            out = {}
            for rec, count, value in Order._read_group(
                    domain + [(by, '!=', False)], [by],
                    ['__count', 'amount_total:sum']):
                out[rec] = (count, value or 0.0)
            return out

        sp_now, sp_prev = grouped(this_week, 'user_id'), \
            grouped(last_week, 'user_id')
        team_now, team_prev = grouped(this_week, 'team_id'), \
            grouped(last_week, 'team_id')

        top_clinics = [
            {'name': partner.display_name, 'orders': count,
             'value': value or 0.0}
            for partner, count, value in Order._read_group(
                this_week + [('partner_id', '!=', False)], ['partner_id'],
                ['__count', 'amount_total:sum'],
                order='amount_total:sum desc', limit=5)]

        by_day = {}
        for when, value in Order._read_group(
                this_week, ['date_order:day'], ['amount_total:sum']):
            by_day[fields.Date.to_date(when)] = value or 0.0

        # Doors opened this week that ALSO ordered this week.
        opened = self.env['res.partner'].sudo().search(
            [('team_id', '!=', False), ('parent_id', '=', False),
             ('create_date', '>=', local_midnight_utc(self.env, date_from)),
             ('create_date', '<',
              local_midnight_utc(self.env, date_to + timedelta(days=1)))])
        fed = Order.search_count(
            this_week + [('partner_id', 'in', opened.ids)]) if opened else 0

        visits = self.env['lab.visit'].sudo().search(
            [('date', '>=', date_from), ('date', '<=', date_to),
             ('state', '=', 'done'), ('company_id', '=', company.id)])
        prev_visits = self.env['lab.visit'].sudo().search(
            [('date', '>=', date_from - timedelta(days=7)),
             ('date', '<', date_from), ('state', '=', 'done'),
             ('company_id', '=', company.id)])

        return {
            'salespeople': [{
                'name': user.name,
                'orders': now[0], 'value': now[1],
                'avg': now[1] / now[0] if now[0] else 0.0,
                'delta': now[1] - sp_prev.get(user, (0, 0.0))[1],
            } for user, now in sorted(sp_now.items(),
                                      key=lambda kv: -kv[1][1])],
            'routes': [{
                'name': team.name,
                'orders': now[0], 'value': now[1],
                'delta': now[1] - team_prev.get(team, (0, 0.0))[1],
            } for team, now in sorted(team_now.items(),
                                      key=lambda kv: -kv[1][1])],
            'top_clinics': top_clinics,
            'best_day': max(by_day.items(), key=lambda kv: kv[1])
                        if by_day else None,
            'zero_days': sum(1 for offset in range((date_to - date_from).days + 1)
                             if (date_from + timedelta(days=offset)).weekday() != 6
                             and not by_day.get(date_from + timedelta(days=offset))),
            'doors_opened': len(opened), 'doors_fed': fed,
            'clinics_visited': len(visits.mapped('partner_id')),
            'clinics_visited_prev': len(prev_visits.mapped('partner_id')),
        }

    @api.model
    def _interpret_week(self, vals, sheets, date_from, date_to, company):
        """The judgements: how the week compares, who stood out, what went
        wrong, how fast the desks were. Computed once, stored forever - a
        report whose verdicts drift after filing is not a record."""
        out = {}
        total = vals['sheet_total'] or 1
        out['approved_pct'] = round(vals['sheet_approved'] * 100.0 / total, 1)
        out['clean_pct'] = round(vals['clean_days'] * 100.0 / total, 1)

        # Against the previous REAL week, if one was filed.
        prev = self.sudo().search(
            [('date_from', '=', date_from - timedelta(days=7)),
             ('company_id', '=', company.id), ('is_interim', '=', False)],
            limit=1)
        out['delta_visits'] = (vals['visits_total'] - prev.visits_total
                               if prev else 0)
        out['delta_collected'] = (vals['collected'] - prev.collected
                                  if prev else 0.0)

        # Who stood out. The star is who brought the most money back; the most
        # improved is whoever grew their OWN visit count the most - a race
        # everyone can win, whatever their route is worth.
        per_user = {u: sheets.filtered(lambda s, x=u: s.user_id == x)
                    for u in sheets.mapped('user_id')}
        collected = {u: sum(mine.mapped('collected'))
                     for u, mine in per_user.items()}
        star = max(collected, key=collected.get) if collected else None
        out['star_exec'] = star.name if star and collected[star] else False

        prev_sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', date_from - timedelta(days=7)),
             ('date', '<', date_from), ('company_id', '=', company.id)])
        gains = {}
        for user, mine in per_user.items():
            before = len(prev_sheets.filtered(lambda s, x=user: s.user_id == x))
            gains[user] = sum(mine.mapped('visit_done_count')) - sum(
                prev_sheets.filtered(
                    lambda s, x=user: s.user_id == x).mapped('visit_done_count'))
        best = max(gains, key=gains.get) if gains else None
        out['improved_exec'] = best.name if best and gains[best] > 0 else False

        out['exceptions_total'] = len(sheets.filtered(lambda s: not s.is_clean))

        def median_hours(frm, to):
            waits = sorted((to(sh) - frm(sh)).total_seconds() / 3600.0
                           for sh in sheets if frm(sh) and to(sh))
            return round(waits[len(waits) // 2], 1) if waits else 0.0

        out['ops_median_h'] = median_hours(lambda sh: sh.submitted_at,
                                           lambda sh: sh.ops_approved_at)
        out['mkt_median_h'] = median_hours(lambda sh: sh.ops_approved_at,
                                           lambda sh: sh.approved_at)
        return out

    @api.model
    def _week_covers(self, date_from, date_to):
        """Who covered a desk during the week - the report must say it, or
        an approval signed by the 'wrong' name reads as a mistake later."""
        if 'lab.desk.cover' not in self.env:
            return self.env['lab.desk.cover']
        return self.env['lab.desk.cover'].sudo().search(
            [('date_from', '<=', date_to), ('date_to', '>=', date_from)])

    @api.model
    def _daily_visits(self, sheets):
        """Visits per day of the week, for the little bars."""
        per_day = {}
        for sheet in sheets:
            per_day[sheet.date] = per_day.get(sheet.date, 0) \
                + sheet.visit_done_count
        return sorted(per_day.items())

    @api.model
    def _focus_points(self, vals, sheets, sales):
        """What to fix NEXT week, as bullets a manager can act on Monday
        morning. Analysis that ends without a to-do list is decoration."""
        points = []
        rescue = self.env['lab.coverage'].sudo().search_count(
            [('order_state', 'in', ('stopped', 'slipping'))]) \
            if 'lab.coverage' in self.env else 0
        if rescue:
            points.append(_('Plan rescue visits: %s clinic(s) are going quiet.',
                            rescue))
        weak = []
        for user in sheets.mapped('user_id'):
            mine = sheets.filtered(lambda sh, u=user: sh.user_id == u)
            visits = sum(mine.mapped('visit_done_count'))
            orders = sum(mine.mapped('order_count'))
            if visits >= 10 and orders * 10 < visits:
                weak.append(user.name)
        if weak:
            points.append(_('Low strike rate (under 1 order per 10 visits): '
                            '%s.', ', '.join(weak[:3])))
        if vals['order_value'] and vals['collected'] < vals['order_value'] * 0.5:
            points.append(_('Collections are behind sales: %(c)s collected '
                            'against %(v)s sold.',
                            c=round(vals['collected']),
                            v=round(vals['order_value'])))
        if sales.get('zero_days'):
            points.append(_('%s working day(s) had no orders at all.',
                            sales['zero_days']))
        if sales.get('doors_opened') and not sales.get('doors_fed'):
            points.append(_('%s new clinic(s) opened but none ordered yet - '
                            'follow up.', sales['doors_opened']))
        if vals['exceptions_total'] > vals['sheet_total'] / 2 and vals['sheet_total']:
            points.append(_('More than half the days had issues - read the '
                            'issues chips above.'))
        return points[:5]

    @api.model
    def _render_insights(self, vals, sheets, sales):
        """The Analysis tab: routes, top doors, strike rates, coverage and
        the focus list - tables and chips, no sentences to wade through."""
        env = self.env
        currency = env.company.currency_id

        def money(amount):
            return format_amount(env, amount or 0.0, currency)

        def h3(text):
            return ('<h3 style="font-size:13px; color:#10233a; '
                    'margin:14px 0 4px;">%s</h3>' % text)

        def table(headers, rows_html):
            head_cells = ''.join(
                '<th style="text-align:%s; padding:4px 8px;">%s</th>' % (
                    'left' if i == 0 else 'right', h)
                for i, h in enumerate(headers))
            return ('<table style="border-collapse:collapse; width:100%%;">'
                    '<thead><tr style="border-bottom:2px solid #cbd5e1; '
                    'color:#64748b; font-size:11px; text-transform:uppercase;">'
                    '%s</tr></thead><tbody>%s</tbody></table>'
                    % (head_cells, rows_html))

        def tr(cells):
            tds = ''.join(
                '<td style="padding:4px 8px; text-align:%s;%s">%s</td>' % (
                    'left' if i == 0 else 'right',
                    ' font-weight:bold;' if i == 0 else '', c)
                for i, c in enumerate(cells))
            return '<tr style="border-bottom:1px solid #eef1f6;">%s</tr>' % tds

        def arrow(value, fmt):
            if not value:
                return '<span style="color:#94a3b8;">=</span>'
            return ('<span style="color:%s; font-weight:bold;">%s %s</span>' % (
                '#0f9d58' if value > 0 else '#dc2626',
                '&#9650;' if value > 0 else '&#9660;', fmt(abs(value))))

        parts = []

        if sales['routes']:
            parts.append(h3(_('Routes, this week vs last')))
            parts.append(table(
                [_('Route'), _('Orders'), _('Value'), _('vs last week')],
                ''.join(tr([html_escape(r['name']), r['orders'],
                            money(r['value']), arrow(r['delta'], money)])
                        for r in sales['routes'])))

        if sales['top_clinics']:
            parts.append(h3(_('Top clinics of the week')))
            parts.append(table(
                [_('Clinic'), _('Orders'), _('Value')],
                ''.join(tr([html_escape(c['name']), c['orders'],
                            money(c['value'])]) for c in sales['top_clinics'])))

        # Strike rate: the quality of the walking, not the amount of it.
        rows = []
        for user in sheets.mapped('user_id').sorted('name'):
            mine = sheets.filtered(lambda sh, u=user: sh.user_id == u)
            visits = sum(mine.mapped('visit_done_count'))
            orders = sum(mine.mapped('order_count'))
            collected = sum(mine.mapped('collected'))
            value = sum(mine.mapped('order_value'))
            rows.append(tr([
                html_escape(user.name), visits, orders,
                ('%.1f' % (orders * 10.0 / visits)) if visits else '&#8212;',
                ('%d%%' % round(collected * 100 / value)) if value else '&#8212;',
            ]))
        if rows:
            parts.append(h3(_('Strike rate — orders per 10 visits, and '
                              'collection vs sales')))
            parts.append(table(
                [_('Executive'), _('Visits'), _('Orders'), _('Strike'),
                 _('Collected/Sold')], ''.join(rows)))

        # Coverage + rhythm + doors: one chip row.
        chips = []

        def chip(text, bg, fg):
            return ('<span style="display:inline-block; background:%s; '
                    'color:%s; border-radius:999px; padding:3px 12px; '
                    'font-size:12px; font-weight:bold; margin:2px 6px 2px 0;">'
                    '%s</span>' % (bg, fg, text))

        delta_cov = sales['clinics_visited'] - sales['clinics_visited_prev']
        chips.append(chip(_('%(n)s clinics visited (%(d)+d vs last week)',
                            n=sales['clinics_visited'], d=delta_cov),
                          '#e0f2fe', '#075985'))
        if sales['best_day']:
            chips.append(chip(_('best day: %(d)s (%(v)s)',
                               d=sales['best_day'][0].strftime('%a %d'),
                               v=money(sales['best_day'][1])),
                          '#bbf7d0', '#14532d'))
        if sales['zero_days']:
            chips.append(chip(_('%s day(s) with no orders', sales['zero_days']),
                              '#fee2e2', '#991b1b'))
        chips.append(chip(_('doors: %(o)s opened, %(f)s already ordered',
                            o=sales['doors_opened'], f=sales['doors_fed']),
                          '#ede9fe', '#5b21b6'))
        parts.append(h3(_('Coverage and rhythm')))
        parts.append('<div>%s</div>' % ''.join(chips))

        focus = self._focus_points(vals, sheets, sales)
        if focus:
            parts.append(h3(_('Fix next week')))
            parts.append('<ol style="margin:4px 0 0 18px; color:#7a1d1d;">%s'
                         '</ol>' % ''.join('<li style="margin-bottom:3px;">%s'
                                           '</li>' % html_escape(f)
                                           for f in focus))
        return ''.join(parts) or ('<p style="color:#94a3b8;">%s</p>'
                                  % _('Nothing to analyse this week.'))

    @api.model
    def _render_text(self, vals, sheets, sales=None):
        """The whole week as plain text. The lab runs on WhatsApp; a report
        that cannot be pasted is a report that does not travel."""
        lines = [
            vals['name'],
            '',
            '%s day sheets, %s%% fully approved, %s%% clean.' % (
                vals['sheet_total'], vals['approved_pct'], vals['clean_pct']),
            '%s visits (%+d vs last week)' % (
                vals['visits_total'], vals['delta_visits']),
            'Collected: %.2f (%+.2f vs last week)' % (
                vals['collected'], vals['delta_collected']),
            'Distance: %.0f km · New clinics: %s' % (
                vals['km_total'], vals['new_clinics']),
        ]
        if vals.get('star_exec'):
            lines.append('Executive of the week: %s' % vals['star_exec'])
        if vals.get('improved_exec'):
            lines.append('Most improved: %s' % vals['improved_exec'])
        if vals['exceptions_total']:
            lines.append('%s day(s) had issues.' % vals['exceptions_total'])
        if sales and sales['salespeople']:
            lines.append('')
            lines.append('Sales:')
            for sp in sales['salespeople']:
                lines.append('- %s: %s orders, %.2f' % (
                    sp['name'], sp['orders'], sp['value']))
        lines.append('')
        lines.append('Field work:')
        for user in sheets.mapped('user_id').sorted('name'):
            mine = sheets.filtered(lambda s, u=user: s.user_id == u)
            lines.append('- %s: %s visits, %.2f collected' % (
                user.name, sum(mine.mapped('visit_done_count')),
                sum(mine.mapped('collected'))))
        for bullet in self._focus_points(vals, sheets, sales or {}):
            lines.append('* ' + bullet)
        return '\n'.join(lines)

    @api.model
    def _render_body(self, vals, sheets, sales=None):
        """The week as tiles, chips, bars and one table - not prose.

        The first version narrated ("Against last week: ..."), and the client
        said exactly what was wrong with that: a senior reader scans, they do
        not read. Everything here is a number with a label, colour-coded, in
        plain tables with inline styles - because this HTML also travels by
        mail, where stylesheets are stripped and divs float badly.
        (client, 2026-08-31)
        """
        env = self.env
        currency = env.company.currency_id

        def money(amount):
            return format_amount(env, amount or 0.0, currency)

        def chip(text, bg, fg='#fff'):
            return ('<span style="display:inline-block; background:%s; '
                    'color:%s; border-radius:999px; padding:3px 12px; '
                    'font-size:12px; font-weight:bold; margin:2px 6px 2px 0; '
                    'white-space:nowrap;">%s</span>' % (bg, fg, text))

        def delta_chip(value, fmt):
            if not value:
                return ('<span style="color:#94a3b8; font-size:11px;">'
                        '= same as last week</span>')
            up = value > 0
            return ('<span style="color:%s; font-size:12px; font-weight:bold;">'
                    '%s %s vs last week</span>' % (
                        '#0f9d58' if up else '#dc2626',
                        '&#9650;' if up else '&#9660;', fmt(abs(value))))

        def tile(value, label, extra=''):
            return ('<td style="background:#f6f8fb; border-radius:10px; '
                    'padding:10px 14px; text-align:center; min-width:90px;">'
                    '<div style="font-size:22px; font-weight:800; '
                    'color:#10233a; line-height:1.1;">%s</div>'
                    '<div style="font-size:11px; color:#64748b; '
                    'text-transform:uppercase; letter-spacing:.06em; '
                    'margin-top:2px;">%s</div>'
                    '<div style="margin-top:3px;">%s</div></td>'
                    '<td style="width:8px;"></td>' % (value, label, extra))

        # ------------------------------------------------------- the tile row
        tiles = (
            '<table style="border-collapse:separate; margin:4px 0 10px;"><tr>'
            + tile(vals['sheet_total'], _('day sheets'))
            + tile('%s%%' % vals['approved_pct'], _('approved'))
            + tile(vals['visits_total'], _('visits'),
                   delta_chip(vals['delta_visits'], str))
            + tile(money(vals['collected']), _('collected'),
                   delta_chip(vals['delta_collected'], money))
            + tile('%s km' % round(vals['km_total']), _('distance'))
            + tile(vals['new_clinics'], _('new clinics'))
            + '</tr></table>')

        # ------------------------------------------- where the sheets stand
        flow = '<div style="margin:2px 0 10px;">' + ''.join([
            chip(_('waiting for Operations: %s', vals['sheet_ops_pending']),
                 '#0e7c86'),
            chip(_('waiting for Marketing: %s', vals['sheet_mkt_pending']),
                 '#7c3aed'),
            chip(_('fully approved: %s', vals['sheet_approved']), '#0f9d58'),
            chip(_('sent back: %s', vals['sheet_sent_back']), '#d99b00'),
        ]) + '</div>'

        # --------------------------------- stars, speed, issues, cover: chips
        badges = []
        if vals.get('star_exec'):
            badges.append(chip('&#11088; %s &#183; %s' % (
                html_escape(vals['star_exec']), _('most collected')),
                '#fde68a', '#92400e'))
        if vals.get('improved_exec'):
            badges.append(chip('&#128200; %s &#183; %s' % (
                html_escape(vals['improved_exec']), _('most improved')),
                '#bbf7d0', '#14532d'))
        if vals['ops_median_h'] or vals['mkt_median_h']:
            badges.append(chip(_('Ops speed: %s h', vals['ops_median_h']),
                               '#e2e8f0', '#334155'))
            badges.append(chip(_('Marketing speed: %s h', vals['mkt_median_h']),
                               '#e2e8f0', '#334155'))
        flag_names = [('flag_gps', _('GPS')), ('flag_open_work', _('left open')),
                      ('flag_zero_visits', _('no visits')),
                      ('flag_km_outlier', _('distance')),
                      ('flag_attendance', _('attendance'))]
        for field, label in flag_names:
            n = len(sheets.filtered(field))
            if n:
                badges.append(chip('&#9888; %s: %s' % (label, n),
                                   '#fee2e2', '#991b1b'))
        for cover in self._week_covers(vals['date_from'], vals['date_to']):
            badges.append(chip(_('cover: %(who)s for %(whom)s',
                                 who=html_escape(cover.delegate_id.name),
                                 whom=html_escape(cover.manager_id.name)),
                               '#fff7e6', '#7a5600'))
        badge_row = ('<div style="margin:0 0 10px;">%s</div>' % ''.join(badges)
                     if badges else '')

        # ------------------------------------------------ visits, day by day
        daily = self._daily_visits(sheets)
        peak = max((n for _d, n in daily), default=0) or 1
        day_cells = ''.join(
            '<td style="text-align:center; padding:2px 6px; '
            'vertical-align:bottom;">'
            '<div style="background:#0e7c86; width:18px; height:%dpx; '
            'margin:0 auto; border-radius:2px 2px 0 0;"></div>'
            '<div style="font-size:11px; color:#666;">%s</div>'
            '<div style="font-size:11px;"><b>%s</b></div></td>' % (
                max(3, round(n * 42.0 / peak)), d.strftime('%a'), n)
            for d, n in daily)
        day_bars = ('<table style="border-collapse:collapse; margin:0 0 12px;">'
                    '<tr>%s</tr></table>' % day_cells) if daily else ''

        # ---------------------------------------------------- the people table
        def bar(pct, colour):
            return ('<div style="background:#eef1f6; border-radius:999px; '
                    'height:7px; width:90px; display:inline-block; '
                    'vertical-align:middle;">'
                    '<div style="background:%s; border-radius:999px; '
                    'height:7px; width:%s%%;"></div></div>' % (
                        colour, max(3, min(100, round(pct)))))

        top_collected = max((sum(sheets.filtered(
            lambda s, u=x: s.user_id == u).mapped('collected'))
            for x in sheets.mapped('user_id')), default=0.0) or 1.0
        rows = []
        for user in sheets.mapped('user_id').sorted('name'):
            mine = sheets.filtered(lambda s, u=user: s.user_id == u)
            approved = mine.filtered(lambda s: s.state == 'approved')
            scored = mine.filtered('marketing_score')
            got = sum(mine.mapped('collected'))
            rows.append(
                '<tr style="border-bottom:1px solid #eef1f6;">'
                '<td style="padding:5px 8px; font-weight:bold;">%s</td>'
                '<td style="text-align:center;">%s</td>'
                '<td style="text-align:center; white-space:nowrap;">%s '
                '<span style="color:#64748b; font-size:11px;">%s/%s</span></td>'
                '<td style="text-align:center;">%s</td>'
                '<td style="text-align:right; white-space:nowrap;">%s %s</td>'
                '<td style="text-align:center;">%s</td>'
                '<td style="text-align:center;">%s</td></tr>' % (
                    html_escape(user.name),
                    sum(mine.mapped('visit_done_count')),
                    bar(len(approved) * 100.0 / len(mine) if mine else 0,
                        '#0f9d58'),
                    len(approved), len(mine),
                    len(mine.filtered('is_clean')),
                    bar(got * 100.0 / top_collected, '#0e7c86'), money(got),
                    sum(mine.mapped('new_clinic_count')),
                    ('&#9733; %.1f' % (sum(int(s.marketing_score)
                                           for s in scored) / len(scored)))
                    if scored else '&#8212;'))
        table = (
            '<table style="border-collapse:collapse; width:100%%;">'
            '<thead><tr style="border-bottom:2px solid #cbd5e1; color:#64748b;'
            ' font-size:11px; text-transform:uppercase;">'
            '<th style="text-align:left; padding:4px 8px;">%s</th>'
            '<th>%s</th><th>%s</th><th>%s</th>'
            '<th style="text-align:right;">%s</th>'
            '<th>%s</th><th>%s</th>'
            '</tr></thead><tbody>%s</tbody></table>' % (
                _('Executive'), _('Visits'), _('Approved'), _('Clean'),
                _('Collected'), _('New'), _('Score'), ''.join(rows))
        ) if rows else ('<p style="color:#94a3b8;">%s</p>'
                        % _('No field activity was recorded this week.'))

        # ------------------------------- sales, by salesperson (the ask)
        sales = sales or {'salespeople': []}
        sp_rows = ''.join(
            '<tr style="border-bottom:1px solid #eef1f6;">'
            '<td style="padding:5px 8px; font-weight:bold;">%s</td>'
            '<td style="text-align:center;">%s</td>'
            '<td style="text-align:right;">%s</td>'
            '<td style="text-align:right; color:#64748b;">%s</td>'
            '<td style="text-align:right;">%s</td></tr>' % (
                html_escape(sp['name']), sp['orders'], money(sp['value']),
                money(sp['avg']), delta_chip(sp['delta'], money))
            for sp in sales['salespeople'])
        sales_table = (
            '<h3 style="font-size:13px; color:#10233a; margin:14px 0 4px;">'
            '%s</h3>'
            '<table style="border-collapse:collapse; width:100%%;">'
            '<thead><tr style="border-bottom:2px solid #cbd5e1; color:#64748b;'
            ' font-size:11px; text-transform:uppercase;">'
            '<th style="text-align:left; padding:4px 8px;">%s</th><th>%s</th>'
            '<th style="text-align:right;">%s</th>'
            '<th style="text-align:right;">%s</th>'
            '<th style="text-align:right;">%s</th></tr></thead>'
            '<tbody>%s</tbody></table>' % (
                _('Sales, by salesperson'), _('Salesperson'), _('Orders'),
                _('Value'), _('Avg / order'), _('vs last week'), sp_rows)
        ) if sales['salespeople'] else ''

        field_head = ('<h3 style="font-size:13px; color:#10233a; '
                      'margin:14px 0 4px;">%s</h3>' % _('Field work, by executive'))
        return (tiles + flow + badge_row + day_bars + sales_table
                + field_head + table)

    # ------------------------------------------------------------------ cron
    def action_open_sheets(self):
        """The week's own evidence: the day sheets the figures were added up
        from, so a reader can verify any number on the report."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Day sheets — %s', self.name),
            'res_model': 'lab.daily.update',
            'view_mode': 'list,form',
            'domain': [('date', '>=', self.date_from),
                       ('date', '<=', self.date_to),
                       ('company_id', '=', self.company_id.id)],
            'context': {'create': False},
        }

    def action_send_again(self):
        """Put the report back in the administrators' inboxes.

        The Monday mail arrives once; the person who was on leave that Monday
        never sees it. One button, same recipients, same body.
        """
        if not self.env.user.has_group('lab_fieldwork.group_fieldwork_admin'):
            raise UserError(_("Only a field work administrator sends the report."))
        admins = self.env.ref('lab_fieldwork.group_fieldwork_admin',
                              raise_if_not_found=False)
        partners = admins.sudo().all_user_ids.filtered('active').partner_id \
            if admins else self.env['res.partner']
        # Sudo for the posting only: the group check above is the guard, and
        # the admins deliberately hold read-not-write on filed reports.
        for report in self.sudo():
            if partners:
                report.message_notify(partner_ids=partners.ids,
                                      subject=report.name,
                                      body=report.body_html)
            report.message_post(body=_("Report sent again to the "
                                       "administrators."))
        return True

    @api.model
    def action_file_week_so_far(self):
        """The week as it stands, filed now, on demand.

        The Monday cron answers "how did last week go" - too late for the
        administrator who wants Wednesday's picture in Wednesday's meeting.
        This files Monday-to-today as an interim snapshot: refiling the same
        week replaces the previous snapshot rather than stacking copies, and
        the real report the cron files on Monday is untouched (different
        date_from, is_interim False).
        """
        if not self.env.user.has_group('lab_fieldwork.group_fieldwork_admin'):
            raise UserError(_("Only a field work administrator files the week."))
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        company = self.env.company
        self.sudo().search(
            [('date_from', '=', monday), ('company_id', '=', company.id),
             ('is_interim', '=', True)]).unlink()
        vals = self._build_week(monday, today, company)
        vals.update(is_interim=True,
                    name=_('%(name)s — so far, filed %(when)s',
                           name=vals['name'], when=today))
        report = self.sudo().create(vals)
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name,
            'res_id': report.id, 'views': [(False, 'form')],
        }

    @api.model
    def _cron_weekly_report(self):
        """Runs daily, acts on Mondays: build last week and hand it to the
        administrators. Daily-and-idempotent beats a weekly schedule that a
        server restart can silently skip."""
        today = fields.Date.context_today(self)
        if today.weekday() != 0:        # Monday builds Mon..Sun behind it
            return False
        date_from = today - timedelta(days=7)
        date_to = today - timedelta(days=1)
        made = self.env['lab.weekly.report']
        for company in self.env['res.company'].sudo().search([]):
            if self.sudo().search_count(
                    [('date_from', '=', date_from),
                     ('company_id', '=', company.id),
                     ('is_interim', '=', False)]):
                continue
            # The real report supersedes any mid-week snapshot of that week.
            self.sudo().search(
                [('date_from', '=', date_from), ('company_id', '=', company.id),
                 ('is_interim', '=', True)]).unlink()
            report = self.sudo().create(
                self.with_company(company)._build_week(date_from, date_to,
                                                       company))
            made |= report
            admins = self.env.ref('lab_fieldwork.group_fieldwork_admin',
                                  raise_if_not_found=False)
            partners = admins.sudo().all_user_ids.filtered('active').partner_id \
                if admins else self.env['res.partner']
            if partners:
                report.message_notify(
                    partner_ids=partners.ids,
                    subject=report.name,
                    body=report.body_html)
        if made:
            _logger.info("weekly field report(s) generated: %s",
                         made.mapped('name'))
        return bool(made)

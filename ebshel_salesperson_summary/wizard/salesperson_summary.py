# Part of ebshel_salesperson_summary. See LICENSE file for full copyright and licensing details.
import io
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL, date_utils
from odoo.tools.misc import format_amount, format_date

PERIODS = [
    ('this_month', 'This month'),
    ('last_month', 'Last month'),
    ('this_quarter', 'This quarter'),
    ('fiscal_year', 'Financial year to date'),
    ('custom', 'Custom'),
]
FIGURES = ('opening', 'billed', 'received', 'adjusted', 'closing')
# The rates the collection pill is coloured by: received out of what was there to collect.
GOOD_RATE, FAIR_RATE = 60.0, 30.0


class SalespersonSummaryWizard(models.TransientModel):
    _name = 'salesperson.summary.wizard'
    _description = 'Salesperson Summary'

    period = fields.Selection(PERIODS, required=True, default='this_month')
    date_from = fields.Date(string='From', required=True,
                            default=lambda self: self._period_dates('this_month')[0])
    date_to = fields.Date(string='To', required=True,
                          default=lambda self: self._period_dates('this_month')[1])
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_ids = fields.Many2many(
        'res.users', string='Salespersons',
        help="Leave empty for every salesperson - customers without one are listed too.")
    layout = fields.Selection([
        ('summary', 'Summary - one line per salesperson'),
        ('detailed', 'Detailed - each salesperson with their customers'),
    ], string='Report', required=True, default='summary')
    page_per_salesperson = fields.Boolean(
        string='New page per salesperson',
        help="Detailed report: start every salesperson on a page of their own, to hand out.")
    summary = fields.Char(compute='_compute_summary')

    # ------------------------------------------------------------------ period
    @api.model
    def _period_dates(self, period, company=None):
        today = fields.Date.context_today(self)
        if period == 'last_month':
            end = date_utils.start_of(today, 'month') - timedelta(days=1)
            return date_utils.start_of(end, 'month'), end
        if period == 'this_quarter':
            return date_utils.start_of(today, 'quarter'), date_utils.end_of(today, 'quarter')
        if period == 'fiscal_year':
            dates = (company or self.env.company).compute_fiscalyear_dates(today)
            return dates['date_from'], today
        return date_utils.start_of(today, 'month'), date_utils.end_of(today, 'month')

    @api.onchange('period', 'company_id')
    def _onchange_period(self):
        if self.period and self.period != 'custom':
            self.date_from, self.date_to = self._period_dates(self.period, self.company_id)

    @api.depends('date_from', 'date_to', 'company_id', 'user_ids')
    def _compute_summary(self):
        for wizard in self:
            if not (wizard.date_from and wizard.date_to and wizard.company_id) \
                    or wizard.date_from > wizard.date_to:
                wizard.summary = False
                continue
            data = wizard._collect()
            wizard.summary = _(
                "%(people)s salesperson(s) · %(customers)s customer(s) · outstanding %(amount)s",
                people=len(data['groups']), customers=data['totals']['customers'],
                amount=format_amount(self.env, data['totals']['closing'], wizard.currency_id))

    # ------------------------------------------------------------------ data
    def _check(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The start date is after the end date."))
        if self.company_id not in self.env.companies:
            raise UserError(_("Choose one of the companies you are working in."))

    def _query(self):
        """Receivable journal items summed per customer (the commercial partner).

        Opening is the balance before the period, closing the balance at its end;
        in between, debits are what was billed and credits are split by where they
        were booked: a bank, cash or card journal (or a payment) is money received,
        anything else - credit notes, write-offs, corrections - is a return or an
        adjustment. So closing = opening + billed - received - adjusted, always.
        """
        self.env.flush_all()
        self.env.cr.execute(SQL("""
            SELECT partner.commercial_partner_id AS customer_id,
                   SUM(CASE WHEN line.date < %(date_from)s THEN line.balance ELSE 0 END) AS opening,
                   SUM(CASE WHEN line.date >= %(date_from)s THEN line.debit ELSE 0 END) AS billed,
                   SUM(CASE WHEN line.date >= %(date_from)s AND %(received)s THEN line.credit ELSE 0 END) AS received,
                   SUM(CASE WHEN line.date >= %(date_from)s AND NOT %(received)s THEN line.credit ELSE 0 END) AS adjusted,
                   SUM(line.balance) AS closing
              FROM account_move_line line
              JOIN account_account account ON account.id = line.account_id
              JOIN account_journal journal ON journal.id = line.journal_id
              JOIN res_partner partner ON partner.id = line.partner_id
             WHERE line.parent_state = 'posted'
               AND account.account_type = 'asset_receivable'
               AND line.company_id = %(company)s
               AND line.date <= %(date_to)s
          GROUP BY partner.commercial_partner_id
        """, date_from=self.date_from, date_to=self.date_to, company=self.company_id.id,
            received=SQL("(journal.type IN ('bank', 'cash', 'credit') OR line.payment_id IS NOT NULL)")))
        return self.env.cr.dictfetchall()

    @api.model
    def _rate(self, received, opening, billed):
        """Received out of what there was to collect (opening + billed), in percent."""
        base = (opening or 0.0) + (billed or 0.0)
        return round(received / base * 100.0, 1) if base > 0.005 else None

    @api.model
    def _place(self, customer):
        """The second line under a customer's name: town, and sales team when there is one."""
        bits = [customer.city or '']
        if 'team_id' in customer._fields and customer.team_id:
            bits.append(customer.team_id.name)
        return ' · '.join(bit for bit in bits if bit)

    def _collect(self):
        """{'groups': [salesperson with its customers and totals], 'totals': {...}}.

        Salespersons are ranked by what is outstanding; customers without one come
        last. A customer with nothing to show - no balance and no movement - is left
        out, as is a salesperson left with no customers.
        """
        self._check()
        currency = self.company_id.currency_id
        rows = self._query()
        customers = self.env['res.partner'].browse([row['customer_id'] for row in rows])
        customers.fetch(['name', 'user_id', 'city'])
        groups = {}
        for row in rows:
            if all(currency.is_zero(row[key] or 0.0) for key in FIGURES):
                continue
            customer = self.env['res.partner'].browse(row['customer_id'])
            user = customer.user_id
            if self.user_ids and user not in self.user_ids:
                continue
            group = groups.setdefault(user.id or 0, dict(
                {key: 0.0 for key in FIGURES}, user=user,
                name=user.name or _("No salesperson"), customers=[], billed_customers=0))
            line = {key: row[key] or 0.0 for key in FIGURES}
            line.update(partner=customer, name=customer.name or '', place=self._place(customer),
                        rate=self._rate(line['received'], line['opening'], line['billed']))
            group['customers'].append(line)
            for key in FIGURES:
                group[key] += line[key]
            group['billed_customers'] += 1 if line['billed'] > 0.005 else 0
        ordered = sorted(groups.values(), key=lambda g: (not g['user'], -g['closing'], g['name']))
        totals = {key: sum(g[key] for g in ordered) for key in FIGURES}
        totals['customers'] = sum(len(g['customers']) for g in ordered)
        totals['rate'] = self._rate(totals['received'], totals['opening'], totals['billed'])
        peak = max([max(g['billed'], g['received']) for g in ordered] or [0.0])
        for rank, group in enumerate(ordered, start=1):
            group['customers'].sort(key=lambda c: (-c['closing'], c['name']))
            group['rank'] = rank
            group['count'] = len(group['customers'])
            group['rate'] = self._rate(group['received'], group['opening'], group['billed'])
            group['share'] = (group['closing'] / totals['closing'] * 100.0
                              if totals['closing'] > 0.005 and group['closing'] > 0 else 0.0)
            # bar lengths for the billed-against-received chart, against the largest figure
            group['billed_bar'] = group['billed'] / peak * 100.0 if peak else 0.0
            group['received_bar'] = group['received'] / peak * 100.0 if peak else 0.0
        # the share bars against the largest share, so the longest bar is the biggest book
        top_share = max([g['share'] for g in ordered] or [0.0])
        for group in ordered:
            group['share_bar'] = group['share'] / top_share * 100.0 if top_share else 0.0
        return {'groups': ordered, 'totals': totals}

    @api.model
    def rate_class(self, rate):
        if rate is None:
            return 'none'
        return 'good' if rate >= GOOD_RATE else 'fair' if rate >= FAIR_RATE else 'low'

    def _people_label(self):
        return ', '.join(self.user_ids.mapped('name')) if self.user_ids else _("All salespersons")

    def _layout(self):
        """The page frame the report is printed in: the company's document layout.
        A database with a letterhead of its own returns its layout here."""
        return 'web.external_layout'

    # ------------------------------------------------------------------ actions
    def _report_action(self):
        self._check()
        return self.env.ref('ebshel_salesperson_summary.action_report_salesperson_summary') \
            .report_action(self, config=False)

    def action_print(self):
        return self._report_action()

    def action_view(self):
        """The same report, on screen."""
        action = self._report_action()
        action['report_type'] = 'qweb-html'
        return action

    def action_export_xlsx(self):
        self._check()
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/salesperson_summary/xlsx/%s' % self.id}

    def _filename(self, extension):
        span = format_date(self.env, self.date_from, date_format='dd-MM-yyyy')
        if self.date_to != self.date_from:
            span += ' to ' + format_date(self.env, self.date_to, date_format='dd-MM-yyyy')
        return _('Salesperson Summary %(span)s.%(ext)s', span=span, ext=extension)

    # ------------------------------------------------------------------ excel
    def _xlsx(self):
        import xlsxwriter  # bundled with Odoo's requirements
        data = self._collect()
        buffer = io.BytesIO()
        book = xlsxwriter.Workbook(buffer, {'in_memory': True})
        accent = '#1F6F8B'
        title = book.add_format({'bold': True, 'font_size': 15, 'font_color': accent})
        muted = book.add_format({'font_color': '#6C757D'})
        head = book.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': accent,
                                'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
        money = book.add_format({'num_format': '#,##0.00'})
        money_b = book.add_format({'num_format': '#,##0.00', 'bold': True, 'bg_color': '#E8F1F5', 'top': 2})
        bold_b = book.add_format({'bold': True, 'bg_color': '#E8F1F5', 'top': 2})
        rate = book.add_format({'num_format': '0.0"%"'})
        rate_b = book.add_format({'num_format': '0.0"%"', 'bold': True, 'bg_color': '#E8F1F5', 'top': 2})
        group_f = book.add_format({'bold': True, 'bg_color': '#F3F6F8'})
        group_money = book.add_format({'num_format': '#,##0.00', 'bold': True, 'bg_color': '#F3F6F8'})
        group_rate = book.add_format({'num_format': '0.0"%"', 'bold': True, 'bg_color': '#F3F6F8'})
        period = _('%(start)s to %(end)s', start=format_date(self.env, self.date_from),
                   end=format_date(self.env, self.date_to))
        figure_heads = [_('Opening'), _('Billed'), _('Received'), _('Returns & Adj.'),
                        _('Outstanding'), _('Collected')]

        def heading(sheet, name, columns):
            sheet.write(0, 0, name, title)
            sheet.write(1, 0, '%s · %s · %s' % (self.company_id.name, period, self._people_label()), muted)
            for col, text in enumerate(columns):
                sheet.write(3, col, text, head)
            sheet.set_row(3, 30)
            sheet.freeze_panes(4, 0)

        def figures(sheet, row, col, values, money_fmt, rate_fmt):
            for offset, key in enumerate(FIGURES):
                sheet.write_number(row, col + offset, values[key], money_fmt)
            if values.get('rate') is not None:
                sheet.write_number(row, col + len(FIGURES), values['rate'], rate_fmt)

        sheet = book.add_worksheet(_('Summary'))
        heading(sheet, _('Salesperson Summary'), ['#', _('Salesperson'), _('Customers')] + figure_heads
                + [_('Share of outstanding')])
        sheet.set_column(0, 0, 5); sheet.set_column(1, 1, 30); sheet.set_column(2, 2, 11)
        sheet.set_column(3, 9, 15); sheet.set_column(10, 10, 13)
        row = 4
        for group in data['groups']:
            sheet.write_number(row, 0, group['rank']); sheet.write(row, 1, group['name'])
            sheet.write_number(row, 2, group['count'])
            figures(sheet, row, 3, group, money, rate)
            sheet.write_number(row, 10, round(group['share'], 1), rate)
            row += 1
        sheet.write(row, 1, _('Total'), bold_b); sheet.write_number(row, 2, data['totals']['customers'], bold_b)
        sheet.write(row, 0, '', bold_b)
        figures(sheet, row, 3, data['totals'], money_b, rate_b)
        sheet.write(row, 10, '', bold_b)
        if data['groups']:
            sheet.autofilter(3, 0, row - 1, 10)

        sheet = book.add_worksheet(_('Customers'))
        heading(sheet, _('Salesperson Summary - customers'),
                [_('Salesperson'), _('Customer'), _('Place')] + figure_heads)
        sheet.set_column(0, 0, 24); sheet.set_column(1, 1, 36); sheet.set_column(2, 2, 22)
        sheet.set_column(3, 8, 15)
        row = 4
        for group in data['groups']:
            for customer in group['customers']:
                sheet.write(row, 0, group['name']); sheet.write(row, 1, customer['name'])
                sheet.write(row, 2, customer['place'])
                figures(sheet, row, 3, customer, money, rate)
                row += 1
            sheet.write(row, 0, group['name'], group_f)
            sheet.write(row, 1, _('%s customer(s)', group['count']), group_f)
            sheet.write(row, 2, '', group_f)
            figures(sheet, row, 3, group, group_money, group_rate)
            row += 1
        sheet.write(row, 0, _('Total'), bold_b); sheet.write(row, 1, '', bold_b); sheet.write(row, 2, '', bold_b)
        figures(sheet, row, 3, data['totals'], money_b, rate_b)
        book.close()
        return buffer.getvalue()

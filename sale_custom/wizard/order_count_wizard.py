# -*- coding: utf-8 -*-
"""Order Count: how much has been billed, how much confirmed work is still unbilled.

The managers' weekly question is not "which order" but "how much did we bill, and how
much confirmed work is still waiting for an invoice" — by route, by salesperson, by
clinic, and usually by two of those at once ("EKM2, and inside it which salesperson").
This wizard answers it on one sheet.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.tools.misc import formatLang

from .order_list_wizard import local_midnight_utc

GROUPS = [
    ('team_id', 'Sales Route'),
    ('user_id', 'Salesperson'),
    ('partner_id', 'Customer'),
]
GROUP_LABELS = dict(GROUPS)


class SaleOrderCountWizard(models.TransientModel):
    _name = 'sale.order.count.report'
    _description = 'Order Count Summary'

    def _default_date_from(self):
        return fields.Date.context_today(self).replace(day=1)

    date_from = fields.Date(string='From', required=True, default=_default_date_from)
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    group_by_1 = fields.Selection(GROUPS, string='Group by', required=True, default='team_id')
    group_by_2 = fields.Selection(GROUPS, string='Then by')
    group_by_3 = fields.Selection(GROUPS, string='And then by')
    team_ids = fields.Many2many('crm.team', string='Sales Routes')
    user_ids = fields.Many2many('res.users', string='Salespeople')
    partner_ids = fields.Many2many('res.partner', string='Customers',
                                   domain="[('customer_rank', '>', 0)]")
    company_id = fields.Many2one('res.company', required=True,
                                 default=lambda self: self.env.company)

    @api.constrains('group_by_1', 'group_by_2', 'group_by_3')
    def _check_groups(self):
        for wiz in self:
            chosen = [g for g in wiz._group_fields()]
            if len(chosen) != len(set(chosen)):
                raise UserError(_('Each level has to group by something different.'))

    @api.onchange('group_by_2')
    def _onchange_group_by_2(self):
        if not self.group_by_2:
            self.group_by_3 = False

    def _group_fields(self):
        self.ensure_one()
        return [g for g in (self.group_by_1, self.group_by_2, self.group_by_3) if g]

    def _check_dates(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('The start date is after the end date.'))

    def _invoice_domain(self):
        """Posted customer invoices and credit notes, by INVOICE date."""
        self.ensure_one()
        self._check_dates()
        domain = [('move_type', 'in', ('out_invoice', 'out_refund')),
                  ('state', '=', 'posted'),
                  ('invoice_date', '>=', self.date_from),
                  ('invoice_date', '<=', self.date_to),
                  ('company_id', '=', self.company_id.id)]
        for field, records in (('team_id', self.team_ids),
                               ('invoice_user_id', self.user_ids),
                               ('partner_id', self.partner_ids)):
            if records:
                domain.append((field, 'in', records.ids))
        return domain

    def _order_domain(self):
        """Confirmed orders not yet fully invoiced, by ORDER date (whole days)."""
        self.ensure_one()
        self._check_dates()
        # The reader's whole days, not UTC ones: a case registered at 01:00 here is
        # still the 1st, not the 31st.
        start = local_midnight_utc(self, self.date_from)
        end = local_midnight_utc(self, self.date_to + timedelta(days=1))
        domain = [('state', 'in', ('sale', 'done')),
                  ('invoice_status', '!=', 'invoiced'),
                  ('date_order', '>=', start), ('date_order', '<', end),
                  ('company_id', '=', self.company_id.id)]
        for field, records in (('team_id', self.team_ids), ('user_id', self.user_ids),
                               ('partner_id', self.partner_ids)):
            if records:
                domain.append((field, 'in', records.ids))
        return domain

    def action_print(self):
        self.ensure_one()
        return self.env.ref('sale_custom.action_report_order_count').report_action(self)


class SaleOrderCountReport(models.AbstractModel):
    _name = 'report.sale_custom.report_order_count'
    _description = 'Order Count renderer'

    def _money(self, company, amount):
        currency = company.currency_id
        return u'%s %s' % (currency.symbol or '',
                           formatLang(self.env, amount or 0.0,
                                      digits=currency.decimal_places))

    # ------------------------------------------------------------------ data
    @api.model
    def _collect(self, wizard):
        """Two document sets, each grouped by its own route/salesperson/customer.

        Definitions (client, 2026-08-19 — replacing the earlier per-order match-up):

        * Billed   = POSTED customer invoices, amount_total (credit notes subtract),
                     period applied to the INVOICE date. Count = number of invoices.
        * Unbilled = CONFIRMED sale orders whose invoice_status is not 'invoiced',
                     amount_total, period applied to the ORDER date (whole days).
                     Count = number of such orders.
        * Total    = Billed + Unbilled, amounts and counts alike — the sheet always
                     foots because Total is literally the sum of the two columns.

        Archived orders stay out (the standard active filter): in this workflow the
        archived confirmed-but-never-invoiced orders are the reworks, which carry
        their original value but will never be billed — pulling them in would
        permanently inflate Unbilled.

        The two sets are independent: an invoice groups by ITS route/salesperson/
        customer, an order by its own. Cancelled documents are excluded by the
        state filters.

        Each query is one round trip: _search builds the filtered, access-rule-
        checked query without running it, and it is spliced in as a CTE.
        """
        rows = []
        move_sql = self.env['account.move']._search(
            wizard._invoice_domain()).select('account_move.id')
        self.env.cr.execute(SQL("""
            WITH moves AS (%s)
            SELECT am.team_id, am.invoice_user_id AS user_id, am.partner_id,
                   SUM(CASE WHEN am.move_type = 'out_refund'
                            THEN -am.amount_total ELSE am.amount_total END) AS billed,
                   COUNT(*) FILTER (WHERE am.move_type = 'out_invoice') AS billed_count
              FROM account_move am
              JOIN moves m ON m.id = am.id
             GROUP BY am.team_id, am.invoice_user_id, am.partner_id
        """, move_sql))
        for row in self.env.cr.dictfetchall():
            rows.append(dict(row, unbilled=0.0, unbilled_count=0))

        order_sql = self.env['sale.order']._search(
            wizard._order_domain()).select('sale_order.id')
        self.env.cr.execute(SQL("""
            WITH orders AS (%s)
            SELECT so.team_id, so.user_id, so.partner_id,
                   SUM(COALESCE(so.amount_total, 0)) AS unbilled,
                   COUNT(*) AS unbilled_count
              FROM sale_order so
              JOIN orders o ON o.id = so.id
             GROUP BY so.team_id, so.user_id, so.partner_id
        """, order_sql))
        for row in self.env.cr.dictfetchall():
            rows.append(dict(row, billed=0.0, billed_count=0))
        return rows

    @api.model
    def _labels(self, rows, levels):
        """Display names for every group key, fetched once per model."""
        models = {'team_id': 'crm.team', 'user_id': 'res.users', 'partner_id': 'res.partner'}
        labels = {}
        for level in levels:
            ids = {r[level] for r in rows if r[level]}
            recs = self.env[models[level]].with_context(active_test=False).browse(list(ids))
            labels[level] = {rec.id: rec.display_name for rec in recs.exists()}
        return labels

    @api.model
    def _tree(self, wizard, rows):
        """Nest the grouped figures under the chosen group levels (up to three)."""
        levels = wizard._group_fields()
        labels = self._labels(rows, levels)

        def blank(label=''):
            return {'label': label, 'count': 0, 'billed_count': 0, 'unbilled_count': 0,
                    'billed': 0.0, 'unbilled': 0.0, 'value': 0.0, 'children': {}}

        root = blank()
        for row in rows:
            node = root
            for level in [None] + levels:
                if level is not None:
                    key = row[level] or 0
                    node = node['children'].setdefault(
                        key, blank(labels[level].get(key, _('None')) if key else _('None')))
                node['billed'] += row['billed'] or 0.0
                node['unbilled'] += row['unbilled'] or 0.0
                node['billed_count'] += row['billed_count'] or 0
                node['unbilled_count'] += row['unbilled_count'] or 0
                # Total is the sum of the two columns, so the sheet always foots.
                node['value'] = node['billed'] + node['unbilled']
                node['count'] = node['billed_count'] + node['unbilled_count']
        return root

    @api.model
    def _flatten(self, node, depth=0, rows=None):
        rows = [] if rows is None else rows
        for child in sorted(node['children'].values(),
                            key=lambda c: (-c['unbilled'], -c['count'], c['label'] or '')):
            rows.append(dict(child, depth=depth))
            self._flatten(child, depth + 1, rows)
        return rows

    def _get_report_values(self, docids, data=None):
        wizard = self.env['sale.order.count.report'].browse(docids)[:1]
        root = self._tree(wizard, self._collect(wizard))
        rows = self._flatten(root)
        company = wizard.company_id

        # What to chase first: the groups holding most of the unbilled value.
        top_level = [r for r in rows if r['depth'] == 0]
        attention = [r for r in sorted(top_level, key=lambda r: -r['unbilled'])
                     if r['unbilled'] > 0][:5]
        positive_unbilled = sum(r['unbilled'] for r in top_level if r['unbilled'] > 0)
        concentration = (sum(r['unbilled'] for r in attention) / positive_unbilled * 100) \
            if positive_unbilled else 0.0

        criteria = []
        for records, label in ((wizard.team_ids, 'Routes'), (wizard.user_ids, 'Salespeople'),
                               (wizard.partner_ids, 'Customers')):
            if records:
                criteria.append((label, ', '.join(records.mapped('name'))))

        return {
            'doc_ids': wizard.ids,
            'doc_model': 'sale.order.count.report',
            'docs': wizard,
            'wizard': wizard,
            'company': company,
            'rows': rows,
            'root': root,
            'levels': [GROUP_LABELS[g] for g in wizard._group_fields()],
            'attention': attention,
            'concentration': concentration,
            'criteria': criteria,
            'money': lambda amount: self._money(company, amount),
            'pct': lambda part, whole: (part / whole * 100) if whole else 0.0,
            'printed_on': fields.Datetime.context_timestamp(
                wizard, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }

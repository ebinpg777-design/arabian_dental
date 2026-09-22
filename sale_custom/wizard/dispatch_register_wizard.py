# -*- coding: utf-8 -*-
"""Daily dispatch register: the day's invoices, route by route, on one printout.

Until now the dispatch table worked from a screenshot of the invoice list grouped by
Sales Route. This wizard prints the same information as a signed document: pick the day
(defaults to today), optionally narrow to a few routes, print. Each route gets its own
block with a tick column for the physical hand-over, and the sheet ends with
prepared / verified / dispatched signature boxes.
"""
from odoo import api, fields, models
from odoo.tools.misc import formatLang


class DispatchRegisterWizard(models.TransientModel):
    _name = 'dental.dispatch.register'
    _description = 'Daily Dispatch Register'

    date = fields.Date(
        string='Dispatch Date', required=True, default=fields.Date.context_today)
    team_ids = fields.Many2many(
        'crm.team', string='Sales Routes',
        help="Leave empty to print every route with invoices on that day.")
    include_draft = fields.Boolean(
        string='Include draft invoices',
        help="Also list invoices that are not posted yet — useful when the register "
             "is printed before the accountant has posted the morning's batch.")
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    def _moves(self):
        self.ensure_one()
        domain = [
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('invoice_date', '=', self.date),
            ('company_id', '=', self.company_id.id),
            ('state', 'in', ('draft', 'posted') if self.include_draft else ('posted',)),
        ]
        if self.team_ids:
            domain.append(('team_id', 'in', self.team_ids.ids))
        return self.env['account.move'].search(domain, order='team_id, name')

    def action_print(self):
        self.ensure_one()
        return self.env.ref('sale_custom.action_report_dispatch_register') \
            .report_action(self)


class DispatchRegisterReport(models.AbstractModel):
    _name = 'report.sale_custom.report_dispatch_register'
    _description = 'Dispatch Register renderer'

    def _money(self, company, amount):
        currency = company.currency_id
        number = formatLang(self.env, amount or 0.0, digits=currency.decimal_places)
        return u'%s %s' % (currency.symbol or '', number)

    @api.model
    def _status(self, move):
        if move.state == 'draft':
            return ('DRAFT', 'draft')
        if move.move_type == 'out_refund':
            return ('CREDIT', 'credit')
        if move.payment_state in ('paid', 'in_payment'):
            return ('PAID', 'paid')
        if move.payment_state == 'partial':
            return ('PART', 'partial')
        if move.payment_state == 'reversed':
            return ('REV', 'draft')
        return ('OPEN', 'open')

    @api.model
    def _works(self, move):
        """The distinct works on the invoice, kept short enough for one cell."""
        names = []
        for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            name = line.product_id.name or (line.name or '').split('\n')[0]
            name = name.strip()
            if name and name not in names:
                names.append(name)
        if len(names) > 2:
            return '%s, %s  +%d more' % (names[0], names[1], len(names) - 2)
        return ', '.join(names)

    def _get_report_values(self, docids, data=None):
        wizards = self.env['dental.dispatch.register'].browse(docids)
        wizard = wizards[:1]
        moves = wizard._moves()
        company = wizard.company_id

        groups = []
        by_team = {}
        for move in moves:
            key = move.team_id.id
            if key not in by_team:
                by_team[key] = {
                    'name': move.team_id.name or 'No Route',
                    'moves': [], 'total': 0.0, 'due': 0.0,
                }
                groups.append(by_team[key])
            sign = -1 if move.move_type == 'out_refund' else 1
            by_team[key]['moves'].append(move)
            by_team[key]['total'] += sign * move.amount_total
            by_team[key]['due'] += sign * move.amount_residual
        # routes with a name first, alphabetically; the unrouted bucket last
        groups.sort(key=lambda g: (g['name'] == 'No Route', g['name']))

        return {
            'doc_ids': wizard.ids,
            'doc_model': 'dental.dispatch.register',
            'docs': wizard,
            'wizard': wizard,
            'company': company,
            'groups': groups,
            'grand_total': sum(g['total'] for g in groups),
            'grand_due': sum(g['due'] for g in groups),
            'invoice_count': len(moves),
            'patient_count': len({p for m in moves for p in m._dental_patients()}),
            'money': lambda amount: self._money(company, amount),
            'status': self._status,
            'works': self._works,
            'printed_on': fields.Datetime.context_timestamp(
                wizard, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }

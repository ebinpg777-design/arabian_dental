# -*- coding: utf-8 -*-
"""Daily dispatch register: the day's invoices, route by route, one route per page.

The sheet the dispatch table works from (client, 2026-10-21: "redesign the dispatch
register based on the attachment ... based on sales route; if no route is selected,
route-wise, each on its own page"). The layout is the v17 one the lab used
(custom_dispatch_print): one row per invoice LINE - invoice, date, doctor, patient,
product - with empty boxes to tick for Invoiced, Packed and Despatch and room for a
remark. What it adds is the route: pick one or more Sales Routes, or none for every
route, and each route starts a page of its own, so the sheet is handed to that route's
runner as a document of its own, with a sign-off strip at its end.
"""
from odoo import _, api, fields, models


class DispatchRegisterWizard(models.TransientModel):
    _name = 'dental.dispatch.register'
    _description = 'Daily Dispatch Register'

    team_ids = fields.Many2many(
        'crm.team', string='Sales Routes',
        help="The routes to print. Leave empty to print every route with invoices, "
             "each route on a page of its own.")
    date = fields.Date(
        string='Invoice Date', required=True, default=fields.Date.context_today)
    date_to = fields.Date(
        string='To',
        help="For invoices of several days - those raised since the last dispatch. "
             "Leave empty for the one day.")
    include_draft = fields.Boolean(
        string='Include draft invoices',
        help="Also list invoices that are not posted yet - useful when the register "
             "is printed before the accountant has posted the morning's batch.")
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    summary = fields.Char(compute='_compute_summary')

    @api.depends('team_ids', 'date', 'date_to', 'include_draft', 'company_id')
    def _compute_summary(self):
        for wizard in self:
            if not wizard.date:
                wizard.summary = ''
                continue
            moves = wizard._moves()
            if not moves:
                wizard.summary = _("No invoices for this selection.")
                continue
            items = sum(len(wizard._lines_of(move)) for move in moves)
            routes = len({move.team_id.id for move in moves})
            wizard.summary = _(
                "%(invoices)s invoice(s) · %(items)s item(s) · %(routes)s route(s), "
                "one page each",
                invoices=len(moves), items=items, routes=routes)

    def _date_to(self):
        self.ensure_one()
        return self.date_to if self.date_to and self.date_to > self.date else self.date

    def _moves(self):
        """The customer invoices of the day (or the range): a credit note is not
        dispatched, so it is not on the register."""
        self.ensure_one()
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('invoice_date', '>=', self.date),
            ('invoice_date', '<=', self._date_to()),
            ('company_id', '=', self.company_id.id),
            ('state', 'in', ('draft', 'posted') if self.include_draft else ('posted',)),
        ]
        if self.team_ids:
            domain.append(('team_id', 'in', self.team_ids.ids))
        return self.env['account.move'].search(domain, order='team_id, name, id')

    @api.model
    def _lines_of(self, move):
        """The invoice's products, a patient's works together (as the v17 sheet had
        them), in the invoice's own order within a patient."""
        lines = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
        return lines.sorted(key=lambda l: ((l.patient or '').strip().upper(), l.sequence, l.id))

    def action_print(self):
        self.ensure_one()
        return self.env.ref('sale_custom.action_report_dispatch_register') \
            .report_action(self)


class DispatchRegisterReport(models.AbstractModel):
    _name = 'report.sale_custom.report_dispatch_register'
    _description = 'Dispatch Register renderer'

    @api.model
    def _product(self, move, line):
        """The product as the invoice prints it, its first line only."""
        label = move._dental_label(line) if line.product_id else (line.name or '')
        return (label or '').strip().split('\n')[0].strip()

    @api.model
    def _invoice_no(self, move):
        """The number, breakable after its last '/', so a narrow column wraps it as
        'B2C/2026-27/' over '19939' rather than anywhere."""
        if move.state == 'draft' and (not move.name or move.name == '/'):
            return _('Draft')
        name = move.name or ''
        head, sep, tail = name.rpartition('/')
        return (head + sep + '​' + tail) if sep else name

    @api.model
    def _groups(self, wizard):
        groups, by_team = [], {}
        for move in wizard._moves():
            key = move.team_id.id or 0
            if key not in by_team:
                by_team[key] = {
                    'name': move.team_id.name or _('No Route'),
                    'routed': bool(move.team_id),
                    'invoices': [], 'invoice_count': 0, 'item_count': 0,
                }
                groups.append(by_team[key])
            rows = [{
                'patient': (line.patient or '').strip(),
                'product': self._product(move, line),
            } for line in wizard._lines_of(move)]
            if not rows:
                # an invoice with no product line still went out the door
                rows = [{'patient': (move.patient_names or '').strip(), 'product': ''}]
            group = by_team[key]
            group['invoices'].append({
                'move': move,
                'number': self._invoice_no(move),
                'draft': move.state == 'draft',
                'date': move.invoice_date.strftime('%d/%m/%Y') if move.invoice_date else '',
                'doctor': move.partner_id.name or '',
                'rows': rows,
            })
            group['invoice_count'] += 1
            group['item_count'] += len(rows)
        # routes with a name first, alphabetically; the unrouted bucket last
        groups.sort(key=lambda g: (not g['routed'], (g['name'] or '').upper()))
        return groups

    def _get_report_values(self, docids, data=None):
        wizard = self.env['dental.dispatch.register'].browse(docids)[:1]
        company = wizard.company_id
        date_to = wizard._date_to()
        date_label = wizard.date.strftime('%d/%m/%Y')
        if date_to != wizard.date:
            date_label = '%s – %s' % (date_label, date_to.strftime('%d/%m/%Y'))
        return {
            'doc_ids': wizard.ids,
            'doc_model': 'dental.dispatch.register',
            'docs': wizard,
            'wizard': wizard,
            'company': company,
            'groups': self._groups(wizard),
            'date_label': date_label,
            'route_filter': ', '.join(wizard.team_ids.mapped('name')),
            'printed_on': fields.Datetime.context_timestamp(
                wizard, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }

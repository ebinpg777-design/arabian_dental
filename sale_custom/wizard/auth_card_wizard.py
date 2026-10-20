# -*- coding: utf-8 -*-
"""Print Authentication Cards (client, 2026-10-20).

One button on orders and invoices - the form and a selection in the list - opens
this wizard: the cards' stock (the lab's pre-printed cards, or blank white ones),
the orders as a list where any one can be left out, the date each card carries,
copies, and a live preview of the cards as they will print. Opened on invoices it
lists the orders they bill, each dated with its invoice, as Odoo 17's card was.
"""
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

PREVIEW_LIMIT = 6


class AuthCardWizard(models.TransientModel):
    _name = 'lab.auth.card.wizard'
    _description = 'Print Authentication Cards'

    stock = fields.Selection(
        [('pre_printed', 'Pre-printed cards'), ('blank', 'Blank cards')],
        string='Card stock', required=True, default='pre_printed',
        help="Pre-printed: only the patient, doctor, job and date are printed, onto "
             "the lab's printed cards (80 x 50 mm, where Odoo 17 printed them). "
             "Blank: the whole card - title, badge, logo, contacts - on a white card.")
    copies = fields.Integer('Copies of each card', default=1, required=True)
    source = fields.Selection([('order', 'Orders'), ('invoice', 'Invoices')], readonly=True)
    line_ids = fields.One2many('lab.auth.card.wizard.line', 'wizard_id', string='Orders')
    order_count = fields.Integer(compute='_compute_counts')
    selected_count = fields.Integer(compute='_compute_counts')
    card_count = fields.Integer(compute='_compute_counts')
    route_count = fields.Integer(compute='_compute_counts')
    preview_html = fields.Html(compute='_compute_preview', sanitize=False)

    # -- opening ------------------------------------------------------------------

    @api.model
    def _open_for(self, records):
        """The wizard on orders or invoices (whatever `records` are)."""
        if not records:
            raise UserError(_("Select the orders or invoices to print authentication cards for."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Authentication Cards'),
            'res_model': self._name,
            'view_mode': 'form',
            'views': [(self.env.ref('sale_custom.view_auth_card_wizard_form').id, 'form')],
            'target': 'new',
            'context': {'active_model': records._name, 'active_ids': records.ids,
                        'dialog_size': 'extra-large'},
        }

    @api.model
    def _route_key(self, order):
        """Grouped by Sales Route, like the stickers, then by order number."""
        team = order.team_id
        return (not team, (team.name or '').lower() if team else '', order.name or '')

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        ctx = self.env.context
        ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
        entries = []                      # (order, invoice or None)
        if ctx.get('active_model') == 'account.move':
            seen = set()
            invoices = self.env['account.move'].browse(ids).exists().filtered(
                lambda m: m.move_type == 'out_invoice')
            for invoice in invoices:
                for order in invoice._card_orders():
                    if order.id not in seen:
                        seen.add(order.id)
                        entries.append((order, invoice))
            values['source'] = 'invoice'
        elif ctx.get('active_model') == 'sale.order':
            orders = self.env['sale.order'].browse(ids).exists().filtered(lambda o: o.state != 'cancel')
            entries = [(order, None) for order in orders]
            values['source'] = 'order'
        entries.sort(key=lambda entry: self._route_key(entry[0]))
        if 'line_ids' in fields_list:
            values['line_ids'] = [Command.create({
                'sequence': index,
                'order_id': order.id,
                'invoice_id': invoice.id if invoice else False,
                'issue_date': (invoice.invoice_date if invoice and invoice.invoice_date
                               else order._card_issue_date()),
            }) for index, (order, invoice) in enumerate(entries)]
        return values

    # -- figures and the preview ----------------------------------------------------

    def _selected_lines(self):
        self.ensure_one()
        return self.line_ids.filtered('selected').sorted(lambda line: (line.sequence, line.id or 0))

    @api.depends('line_ids.selected', 'copies')
    def _compute_counts(self):
        for wiz in self:
            chosen = wiz.line_ids.filtered('selected')
            wiz.order_count = len(wiz.line_ids)
            wiz.selected_count = len(chosen)
            wiz.card_count = len(chosen) * max(wiz.copies, 1)
            wiz.route_count = len(chosen.order_id.team_id)

    @api.depends('stock', 'copies', 'line_ids.selected', 'line_ids.issue_date')
    def _compute_preview(self):
        for wiz in self:
            lines = wiz._selected_lines()
            cards = [line.order_id._card_values(issue_date=line.issue_date)
                     for line in lines[:PREVIEW_LIMIT]]
            company = (lines[:1].order_id.company_id or self.env.company)
            # the logo by URL (the browser caches it): inline, every refresh of the
            # preview carried it once per card - 200 KB a click
            logo = ('/web/image/res.company/%d/%s' % (company.id, 'logo1' if company.logo1 else 'logo')
                    if (company.logo1 or company.logo) else '')
            wiz.preview_html = self.env['ir.qweb']._render('sale_custom.auth_card_wizard_preview', {
                'cards': cards,
                'more': max(len(lines) - PREVIEW_LIMIT, 0),
                'blank': wiz.stock == 'blank',
                'copies': max(wiz.copies, 1),
                'logo': logo,
                'emails': cards[0]['emails'] if cards else [],
                'website': cards[0]['website'] if cards else '',
            }) if cards else Markup(
                '<div class="o_lab_card_empty">%s</div>') % _("Tick at least one order to see its card.")

    # -- buttons ---------------------------------------------------------------------

    def _reopen(self):
        """Stay on the wizard: a button that returns nothing closes the dialog."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Authentication Cards'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('sale_custom.view_auth_card_wizard_form').id, 'form')],
            'target': 'new',
            'context': dict(self.env.context, dialog_size='extra-large'),
        }

    def action_select_all(self):
        self.line_ids.selected = True
        return self._reopen()

    def action_select_none(self):
        self.line_ids.selected = False
        return self._reopen()

    def _cards_to_print(self):
        """[(order, card values)] in print order, copies side by side."""
        self.ensure_one()
        cards = []
        for line in self._selected_lines():
            card = line.order_id._card_values(issue_date=line.issue_date)
            cards.extend((line.order_id, card) for _copy in range(max(self.copies, 1)))
        return cards

    def action_print(self):
        self.ensure_one()
        if not self.line_ids.filtered('selected'):
            raise UserError(_("Tick at least one order to print its card."))
        if self.copies < 1:
            raise UserError(_("Print at least one copy of each card."))
        report = self.env.ref('sale_custom.action_report_auth_card_wizard_values'
                              if self.stock == 'pre_printed'
                              else 'sale_custom.action_report_auth_card_wizard_blank')
        return report.report_action(self, config=False)


class AuthCardWizardLine(models.TransientModel):
    _name = 'lab.auth.card.wizard.line'
    _description = 'Authentication card to print'
    _order = 'sequence, id'

    wizard_id = fields.Many2one('lab.auth.card.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    selected = fields.Boolean('Print', default=True)
    order_id = fields.Many2one('sale.order', string='Order', required=True, readonly=True)
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True)
    patient = fields.Char(related='order_id.patient', string='Patient')
    route_id = fields.Many2one(related='order_id.team_id', string='Sales Route')
    doctor = fields.Char('Doctor', compute='_compute_doctor')
    issue_date = fields.Date('Issue date',
                             help="The date printed on the card: the invoice date when opened on "
                                  "invoices, else the day the case went out (or the order day).")

    @api.depends('order_id')
    def _compute_doctor(self):
        for line in self:
            line.doctor = line.order_id._card_values()['doctor'] if line.order_id else ''

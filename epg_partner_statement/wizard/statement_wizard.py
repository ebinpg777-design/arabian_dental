# -*- coding: utf-8 -*-
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.statement_engine import PERIOD_PRESETS, STATEMENT_TYPES


class PartnerStatementWizard(models.TransientModel):
    _name = 'epg.partner.statement.wizard'
    _description = 'Partner Statement'

    partner_ids = fields.Many2many(
        'res.partner', string='Partners',
        default=lambda self: self._default_partners(),
        help="Tick the partners the statement is for. Leave empty and tick 'Every partner "
             "with a balance' for a full run.")
    team_ids = fields.Many2many(
        'crm.team', string='Sales Routes',
        help="Print every clinic on these routes. Leave the partner list empty and the "
             "whole route is printed, one statement per page, in route order.")
    min_outstanding = fields.Monetary(
        string='Skip outstanding below', default=1.0, currency_field='currency_id',
        help="Customers whose outstanding on the end date is under this amount are left "
             "out of the run — nobody posts a statement for 50 paise. Set it to 0 to "
             "print everyone.")
    currency_id = fields.Many2one(related='company_id.currency_id')
    include_empty = fields.Boolean(
        string='Include clinics with no entries',
        help="Off: a clinic on the route that has nothing on its ledger up to the end "
             "date is skipped instead of printing an empty statement.")
    all_with_balance = fields.Boolean(
        string='Every partner with an open balance',
        help="Ignore the list above and take every partner that still owes (or is owed) "
             "something on the chosen date - the month-end mailing run.")
    statement_type = fields.Selection(
        STATEMENT_TYPES, string='Statement', required=True,
        default=lambda self: self.env['ir.config_parameter'].sudo().get_param(
            'epg_partner_statement.default_type', 'receivable'))
    period = fields.Selection(PERIOD_PRESETS, string='Period', required=True, default='this_month')
    date_from = fields.Date(string='From', required=True,
                            default=lambda self: self.env['epg.partner.statement'].period_dates('this_month')[0])
    date_to = fields.Date(string='To', required=True,
                          default=lambda self: self.env['epg.partner.statement'].period_dates('this_month')[1])
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    open_items_only = fields.Boolean(
        string='Open items only',
        help="List only what is still unpaid (with days overdue and the amount left) instead "
             "of the full ledger for the period.")
    show_ageing = fields.Boolean(string='Ageing summary', default=False)
    # WHEN IT WAS TYPED, not what it is dated. On this ledger the two are years
    # apart for migrated paper, and the office needs both questions: "the
    # statement for August" and "only what we have entered since the import".
    # Empty means no bound at either end. (client, 2026-09-02)
    created_from = fields.Datetime(
        string='Entered after',
        help="Only journal entries CREATED in Odoo at or after this moment. This is "
             "when the entry was typed, not the date it carries - use it to leave a "
             "migration or a bulk import out of the statement. Leave empty for no limit.")
    created_to = fields.Datetime(
        string='Entered before',
        help="Only journal entries created in Odoo at or before this moment. Leave "
             "empty for no limit.")
    partner_count = fields.Integer(compute='_compute_partner_count')
    email_count = fields.Integer(compute='_compute_partner_count')

    @api.model
    def _default_partners(self):
        ctx = self.env.context
        if ctx.get('active_model') == 'res.partner':
            ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
            return self.env['res.partner'].browse(ids).exists().ids
        return []

    @api.depends('partner_ids', 'team_ids', 'include_empty', 'all_with_balance',
                 'min_outstanding', 'statement_type', 'date_to', 'company_id',
                 'created_from', 'created_to')
    def _compute_partner_count(self):
        for wiz in self:
            partners = wiz._partners()
            wiz.partner_count = len(partners)
            wiz.email_count = len(partners.filtered('email'))

    @api.onchange('period', 'company_id')
    def _onchange_period(self):
        if self.period and self.period != 'custom':
            d1, d2 = self.env['epg.partner.statement'].period_dates(self.period, self.company_id)
            self.date_from, self.date_to = d1, d2

    @api.onchange('date_from', 'date_to')
    def _onchange_dates(self):
        d1, d2 = self.env['epg.partner.statement'].period_dates(self.period, self.company_id)
        if self.period != 'custom' and (self.date_from, self.date_to) != (d1, d2):
            self.period = 'custom'

    # ------------------------------------------------------------------ helpers
    def _options(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The start date is after the end date."))
        if self.created_from and self.created_to \
                and self.created_from > self.created_to:
            raise UserError(_(
                "'Entered after' is later than 'Entered before', so no entry "
                "can match both."))
        return {
            'statement_type': self.statement_type,
            'date_from': self.date_from, 'date_to': self.date_to,
            'company': self.company_id,
            'open_items_only': self.open_items_only,
            'show_ageing': self.show_ageing,
            'created_from': self.created_from or None,
            'created_to': self.created_to or None,
        }

    def _account_types(self):
        return {'receivable': ('asset_receivable',), 'payable': ('liability_payable',),
                'both': ('asset_receivable', 'liability_payable')}[self.statement_type or 'receivable']

    def _entered_leaves(self):
        """The entered-between leaves, for the SELECTION queries.

        The same leaves the statement itself is built from (the engine owns
        the definition): a clinic must not be picked for a run whose statement
        then comes out empty because every one of its entries was typed
        outside the window. (client, 2026-09-02)
        """
        return self.env['epg.partner.statement']._entered_domain({
            'created_from': self.created_from or None,
            'created_to': self.created_to or None,
        })

    def _partners_with_ledger(self, partners):
        """Of `partners`, the ones that have anything on their ledger by the end date.

        Dated the same way the statement itself is — on the document's own date where
        one was carried over — so the run cannot pick a clinic whose statement then
        comes out empty, or skip one whose does not. (client, 2026-08-27)
        """
        groups = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'), ('company_id', '=', self.company_id.id),
             ('account_id.account_type', 'in', self._account_types()),
             ('statement_date', '<=', self.date_to or fields.Date.today()),
             ('partner_id', 'in', partners.ids)] + self._entered_leaves(),
            ['partner_id'], ['__count'])
        return self.env['res.partner'].browse([p.id for p, _count in groups])

    def _route_partners(self):
        """Every clinic on the chosen routes — the route-wise run (client, 2026-08-19)."""
        partners = self.env['res.partner'].search([
            '|', ('team_id', 'in', self.team_ids.ids),
            '&', ('team_id', '=', False), ('commercial_partner_id.team_id', 'in', self.team_ids.ids),
        ])
        if not self.include_empty:
            partners = self._partners_with_ledger(partners)
        return partners

    def _sorted_by_route(self, partners):
        """Route order, then clinic name: the run comes off the printer route by route."""
        return partners.sorted(
            key=lambda p: ((p.team_id.name or p.commercial_partner_id.team_id.name or '~'),
                           (p.name or '')))

    def _above_threshold(self, partners):
        """Drop partners whose outstanding as of the end date is under the floor.

        Outstanding = unreconciled residual on the statement's account types, the same
        figure the statement itself prints as "amount due". Applied to every selection
        path — hand-picked partners included: the floor is about not POSTING trivial
        statements, however the list was built. abs() so a payable run (negative sign)
        and clinics we owe are judged by size, not sign.
        """
        self.ensure_one()
        floor = self.min_outstanding or 0.0
        if not partners or floor <= 0:
            return partners
        groups = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'), ('company_id', '=', self.company_id.id),
             ('account_id.account_type', 'in', self._account_types()),
             ('statement_date', '<=', self.date_to or fields.Date.today()),
             ('partner_id', 'in', partners.ids), ('reconciled', '=', False)]
            + self._entered_leaves(),
            ['partner_id'], ['amount_residual:sum'])
        keep = {p.id for p, total in groups if abs(total or 0.0) >= floor}
        return partners.filtered(lambda p: p.id in keep)

    def _partners(self):
        self.ensure_one()
        if self.all_with_balance:
            groups = self.env['account.move.line']._read_group(
                [('parent_state', '=', 'posted'), ('company_id', '=', self.company_id.id),
                 ('account_id.account_type', 'in', self._account_types()),
                 ('statement_date', '<=', self.date_to or fields.Date.today()),
                 ('partner_id', '!=', False), ('reconciled', '=', False)]
                + self._entered_leaves(),
                ['partner_id'], ['amount_residual:sum'])
            ids = [p.id for p, total in groups if not self.company_id.currency_id.is_zero(total)]
            partners = self.env['res.partner'].browse(ids)
            if self.team_ids:
                partners = partners.filtered(
                    lambda p: (p.team_id or p.commercial_partner_id.team_id) in self.team_ids)
            return self._sorted_by_route(self._above_threshold(partners))
        if self.partner_ids:
            partners = self.partner_ids
            if self.team_ids:      # both given: the ticked clinics that are on those routes
                partners = partners.filtered(
                    lambda p: (p.team_id or p.commercial_partner_id.team_id) in self.team_ids)
            return self._sorted_by_route(self._above_threshold(partners))
        if self.team_ids:          # no clinic ticked: the whole route
            return self._sorted_by_route(self._above_threshold(self._route_partners()))
        return self.env['res.partner']

    def _checked_partners(self):
        partners = self._partners()
        if not partners:
            raise UserError(_(
                "Nothing to print: pick a partner, pick a Sales Route, or tick "
                "'Every partner with an open balance'."))
        return partners

    # ------------------------------------------------------------------ actions
    def action_print(self):
        partners = self._checked_partners()
        options = self._options()
        return self.env.ref('epg_partner_statement.action_report_partner_statement').report_action(
            partners, data={
                'ids': partners.ids, 'statement_type': options['statement_type'],
                'date_from': str(options['date_from']), 'date_to': str(options['date_to']),
                'company_id': options['company'].id, 'open_items_only': options['open_items_only'],
                'show_ageing': options['show_ageing'],
                **self.env['epg.partner.statement'].entered_data(options)},
            config=False)

    def action_export_xlsx(self):
        partners = self._checked_partners()
        options = self._options()
        content = self.env['epg.partner.statement'].render_xlsx(partners, options)
        name = _('Statements %(start)s to %(end)s.xlsx', start=options['date_from'], end=options['date_to'])
        if len(partners) == 1:
            name = _('Statement %(name)s %(start)s to %(end)s.xlsx', name=partners.name,
                     start=options['date_from'], end=options['date_to'])
        attachment = self.env['ir.attachment'].create({
            'name': name, 'type': 'binary', 'datas': base64.b64encode(content),
            'res_model': self._name, 'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/web/content/%s?download=true' % attachment.id}

    def action_send_email(self):
        partners = self._checked_partners()
        options = self._options()
        sent = self.env['epg.partner.statement'].send_by_email(partners, options)
        skipped = partners - sent
        message = _("%s statement(s) e-mailed.", len(sent))
        if skipped:
            message += ' ' + _("No e-mail address: %s.", ', '.join(skipped.mapped('display_name')[:8]))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _('Statements'), 'message': message,
                       'type': 'success' if sent else 'warning', 'sticky': bool(skipped),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }

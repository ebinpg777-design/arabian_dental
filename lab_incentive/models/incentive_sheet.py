# -*- coding: utf-8 -*-
"""One executive's incentive for one month — calculated, approved, disbursed as three
separate, deliberate steps (client rule, 2026-08-09), because a figure that pays out
the moment it is computed has no room for anyone to catch a mistake before the money
moves.
"""
from datetime import datetime, time

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .product_template import INCENTIVE_BASES

EXCLUDED_STATES = ('draft', 'sent', 'cancel')


class LabIncentiveSheet(models.Model):
    _name = 'lab.incentive.sheet'
    _description = 'Incentive Sheet'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'period desc, executive_id'
    _rec_name = 'name'

    name = fields.Char(default=lambda s: _('New'), copy=False, readonly=True)
    executive_id = fields.Many2one('res.users', required=True, tracking=True,
                                   index=True)
    period = fields.Date(
        required=True, tracking=True,
        help="Any date in the month this sheet covers — stored as the 1st.")
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    rule_id = fields.Many2one('lab.incentive.rule', readonly=True, copy=False,
                              string='Rule Applied')

    state = fields.Selection(
        [('draft', 'Draft'), ('computed', 'Computed'),
         ('approved', 'Approved'), ('paid', 'Paid')],
        default='draft', required=True, tracking=True, copy=False)

    line_ids = fields.One2many('lab.incentive.sheet.line', 'sheet_id', readonly=True)
    total_sales = fields.Monetary(
        'Total Billed', compute='_compute_totals', store=True,
        help="What was billed to the clinics in the month: the posted invoices of the "
             "executive's orders, less their credit notes.")
    total_base = fields.Monetary(
        compute='_compute_totals', store=True,
        help="What the incentive was actually calculated on — differs from Total "
             "Billed whenever a line's product is counted on its own incentive price "
             "or on the lab's margin.")
    # What the slabs gave on the base - the whole of it, kept on the sheet: the lines
    # no longer carry a share of it, which read as if each order earned its own.
    # (client, 2026-10-08)
    slab_amount = fields.Monetary('Slab Incentive', readonly=True, copy=False)
    incentive_amount = fields.Monetary(compute='_compute_totals', store=True)
    # The doors opened this month, and what they were worth. Stored on the sheet
    # rather than recomputed on read, so an approved sheet keeps the figure it was
    # approved on even if a clinic is later moved to another route.
    # (client, 2026-08-27)
    new_clinic_count = fields.Integer('New Clinics', readonly=True, copy=False)
    # The clinics themselves, as counted: the smart button lists these and not a fresh
    # search, so an approved sheet shows the clinics it was approved on.
    new_clinic_ids = fields.Many2many(
        'res.partner', 'lab_incentive_sheet_new_clinic_rel', 'sheet_id', 'partner_id',
        string='New Clinic List', readonly=True, copy=False)
    new_clinic_amount = fields.Monetary('New Clinic Bonus', readonly=True, copy=False,
                                        currency_field='currency_id')

    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    paid_date = fields.Datetime(readonly=True, copy=False)
    paid_reference = fields.Char(copy=False)

    _uniq_exec_period = models.Constraint(
        'unique(executive_id, period, company_id)',
        'There is already an incentive sheet for this executive and month.')

    @api.depends('line_ids.sale_amount', 'line_ids.incentive_base',
                 'slab_amount', 'new_clinic_amount')
    def _compute_totals(self):
        for sheet in self:
            sheet.total_sales = sum(sheet.line_ids.mapped('sale_amount'))
            sheet.total_base = sum(sheet.line_ids.mapped('incentive_base'))
            # The clinic bonus rides on top of the slabs rather than into the base:
            # putting it into the base would let opening a door push the executive up a
            # sales slab, which is not what a sales slab measures.
            sheet.incentive_amount = (sheet.slab_amount or 0.0) + (sheet.new_clinic_amount or 0.0)

    def _new_clinics(self):
        """The clinics this executive brought in that month: customers marked as a
        new lead by their salesperson, whose Salesperson is this executive, registered
        in the month.

        The mark decides, not who typed the clinic in or on whose route it sits: the
        office registers clinics as often as the field does, and nobody on the lab's
        book has a route, so the old rule (creator + own route) counted nothing.
        (client, 2026-10-08)
        """
        self.ensure_one()
        start, end = self._month_window(self.period)
        return self.env['res.partner'].sudo().search([
            ('incentive_new_lead', '=', True),
            ('user_id', '=', self.executive_id.id),
            # the clinic itself, not a doctor or an address filed under it: a ticked
            # contact is not another clinic (2026-10-08)
            ('parent_id', '=', False),
            ('create_date', '>=', start),
            ('create_date', '<', end),
            ('company_id', 'in', (False, self.company_id.id)),
        ])

    def action_view_new_clinics(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('New clinics — %s', self.name),
            'res_model': 'res.partner', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', self.sudo().new_clinic_ids.ids)],
            'context': {'create': False},
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'lab.incentive.sheet') or _('New')
            if vals.get('period'):
                # Always the 1st, whatever day was picked — "the month" is the unit,
                # and the uniqueness constraint depends on every sheet for a month
                # landing on the same date.
                vals['period'] = fields.Date.to_date(vals['period']).replace(day=1)
        return super().create(vals_list)

    # ------------------------------------------------------------------ windows
    @api.model
    def _lab_tz(self):
        """The lab's clock: context tz, then the user's, then the company's.

        The user's tz alone fixes nothing here - 129 of 156 users have none - and a
        month read at midnight UTC starts at 05:30 in the lab, so the first hours of
        the 1st were paid in the previous month.
        """
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def _month_window(self, period):
        """(start, end) of the local month holding `period`, as naive UTC datetimes
        for a datetime column like `date_order`; `end` is exclusive."""
        tz = self._lab_tz()
        first = fields.Date.to_date(period).replace(day=1)

        def utc(day):
            return tz.localize(datetime.combine(day, time.min)).astimezone(
                pytz.utc).replace(tzinfo=None)
        return utc(first), utc(first + relativedelta(months=1))

    # ------------------------------------------------------------------ basis
    @api.model
    def _incentive_basis(self):
        """'invoiced' (billed) or 'paid' (billed and paid). Only billed amounts count
        (client, 2026-10-08), so the older 'confirmed' and 'delivered' read as billed."""
        basis = self.env['ir.config_parameter'].sudo().get_str('lab_incentive.basis')
        return 'paid' if basis == 'paid' else 'invoiced'

    @api.model
    def _paid_invoices(self, invoices, company):
        """The invoices the clinic's receipts have fully reached.

        `payment_state` never reaches 'paid' on this ledger - receipts are plain
        journal entries that are never reconciled - so the Collections countback is
        the only honest answer to "is this paid".
        """
        if not invoices:
            return invoices
        # The countback is raw SQL: what this transaction posted must be written first.
        self.env['account.move'].flush_model()
        self.env['account.move.line'].flush_model()
        open_moves = {d['move_id'] for d in self.env['lab.collection.performance']
                      .sudo()._open_debits(
                          company, partner_ids=invoices.commercial_partner_id.ids)}
        return invoices.filtered(lambda m: m.id not in open_moves)

    def _qualifying_lines(self):
        """The billed lines this executive's incentive is judged on for the month:
        every product line of a posted customer invoice or credit note dated in the
        month, for an order whose Salesperson is the executive.

        Billed, not ordered: an order counts for what was invoiced on it, a credit
        note takes its amount back, and an order not yet invoiced counts in the month
        it is. Elevated: a manager computing sheets need not hold accounting rights.
        """
        self.ensure_one()
        first = fields.Date.to_date(self.period).replace(day=1)
        lines = self.env['account.move.line'].sudo().search([
            ('move_id.state', '=', 'posted'),
            ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
            ('move_id.invoice_date', '>=', first),
            ('move_id.invoice_date', '<', first + relativedelta(months=1)),
            # A sheet is one company's promise: without this, company 2's orders
            # were paid under company 1's slabs.
            ('move_id.company_id', '=', self.company_id.id),
            ('display_type', '=', 'product'),
            ('sale_line_ids.order_id.user_id', '=', self.executive_id.id),
            ('sale_line_ids.order_id.state', 'not in', EXCLUDED_STATES),
            ('sale_line_ids.order_id.is_rework', '=', False),
        ], order='date, move_id, id')
        if self._incentive_basis() == 'paid':
            invoices = lines.move_id.filtered(lambda m: m.move_type == 'out_invoice')
            paid = self._paid_invoices(invoices, self.company_id)
            # a credit note always counts: it takes back what its invoice billed
            lines = lines.filtered(lambda l: l.move_id.move_type == 'out_refund'
                                   or l.move_id in paid)
        return lines

    def action_compute(self):
        for sheet in self:
            if sheet.state == 'paid':
                raise UserError(_("%s is already paid — it cannot be recomputed.",
                                  sheet.name))
            if sheet.state == 'approved':
                raise UserError(_("%s is approved. Reset it before recomputing, so the "
                                  "approval is not lost without anyone noticing.",
                                  sheet.name))
            sheet.line_ids.unlink()
            # the rule first: a product on a margin basis needs the period's fallback
            rule = self.env['lab.incentive.rule']._active_rule(
                sheet.company_id, sheet.period)
            default_margin = rule.default_margin_pct if rule else 0.0
            lines = []
            for aml in sheet._qualifying_lines():
                # company currency, signed: a credit note is a negative bill
                billed = -aml.balance
                sign = -1 if billed < 0 else 1
                quantity = abs(aml.quantity) * sign
                product = aml.product_id
                # What the product is counted on (Ortho, 2026-10-08): the billed
                # amount, its incentive price, or the lab's margin on it - signed
                # like the bill, so a credit note takes back the base it gave.
                base = (product._incentive_base(billed, quantity, default_margin)
                        if product else billed)
                lines.append((0, 0, {
                    'sale_order_id': aml.sale_line_ids.order_id[:1].id,
                    'invoice_id': aml.move_id.id,
                    'invoice_date': aml.move_id.invoice_date,
                    'product_id': product.id,
                    'quantity': quantity,
                    'sale_amount': billed,
                    'incentive_base': base,
                    'incentive_basis': product.incentive_basis or 'sale',
                    'used_incentive_price': bool(product) and product._uses_own_incentive_base(),
                }))
            clinics = sheet._new_clinics()
            bonus = len(clinics) * (rule.new_clinic_bonus or 0.0) if rule else 0.0
            sheet.write({'line_ids': lines, 'state': 'computed', 'rule_id': rule.id,
                         'new_clinic_ids': [(6, 0, clinics.ids)],
                         'new_clinic_count': len(clinics),
                         'new_clinic_amount': bonus})
            if not rule:
                sheet.slab_amount = 0.0
                sheet.message_post(body=_(
                    "No incentive rule is active for %s — the incentive amount is "
                    "zero until one is configured.", sheet.period.strftime('%B %Y')))
                continue
            sheet.slab_amount = rule.sudo().compute_incentive(sheet.total_base)
        return True

    def action_approve(self):
        for sheet in self:
            if sheet.state != 'computed':
                raise UserError(_(
                    "%s must be computed before it can be approved.", sheet.name))
            if not self.env.user.has_group('lab_fieldwork.group_fieldwork_manager'):
                raise UserError(_(
                    "Only a manager or CEO may approve an incentive sheet."))
            sheet.write({
                'state': 'approved', 'approved_by_id': self.env.uid,
                'approved_date': fields.Datetime.now(),
            })
            sheet.executive_id.partner_id and sheet.message_notify(
                partner_ids=sheet.executive_id.partner_id.ids,
                body=_("Your incentive for %(month)s has been approved: %(amt)s.",
                       month=sheet.period.strftime('%B %Y'),
                       amt=sheet.currency_id.format(sheet.incentive_amount)))
        return True

    def action_disburse(self):
        for sheet in self:
            if sheet.state != 'approved':
                raise UserError(_("%s must be approved before it can be paid.",
                                  sheet.name))
            if not sheet.paid_reference:
                raise UserError(_(
                    "Record a payment reference before marking %s paid — that is "
                    "what the executive's statement points to.", sheet.name))
            sheet.write({'state': 'paid', 'paid_date': fields.Datetime.now()})
        return True

    @api.model
    def _cron_generate_last_month(self):
        """The scheduled path (opt-in, off by default) for the same generation the
        month-end wizard runs by hand."""
        if self.env['ir.config_parameter'].sudo().get_str(
                'lab_incentive.auto_generate') != 'True':
            return
        today = fields.Date.context_today(self)
        period = fields.Date.subtract(today, months=1).replace(day=1)
        self.env['lab.incentive.generate.wizard'].create(
            {'period': period}).action_generate()

    def action_reset_to_draft(self):
        for sheet in self:
            if sheet.state == 'paid':
                raise UserError(_(
                    "%s is already paid and cannot be reopened — raise a "
                    "correction on next month's sheet instead.", sheet.name))
            sheet.write({'state': 'draft', 'approved_by_id': False,
                        'approved_date': False})
        return True


class LabIncentiveSheetLine(models.Model):
    _name = 'lab.incentive.sheet.line'
    _description = 'Incentive Sheet Line'

    sheet_id = fields.Many2one('lab.incentive.sheet', required=True,
                               ondelete='cascade')
    sale_order_id = fields.Many2one('sale.order', required=True)
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True)
    invoice_date = fields.Date(readonly=True)
    product_id = fields.Many2one('product.product', required=True)
    quantity = fields.Float(digits='Product Unit', readonly=True)
    sale_amount = fields.Monetary(
        'Billed Amount', currency_field='currency_id',
        help="What this invoice line billed, before tax; negative on a credit note.")
    incentive_base = fields.Monetary(
        currency_field='currency_id',
        help="What the incentive was actually computed on for this line.")
    # Written when the sheet is computed, not related to the product: an approved
    # sheet keeps the basis it was approved on if the product is changed later.
    incentive_basis = fields.Selection(
        INCENTIVE_BASES, string='Counted On', readonly=True,
        help="What this product's base was taken from when the sheet was computed.")
    used_incentive_price = fields.Boolean(
        string='Own Base',
        help="This product carries its own incentive base — a fixed price or a "
             "margin — so the base above is not what the doctor was charged.")
    currency_id = fields.Many2one(related='sheet_id.currency_id')

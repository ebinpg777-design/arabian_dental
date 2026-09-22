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
    total_sales = fields.Monetary(compute='_compute_totals', store=True)
    total_base = fields.Monetary(
        compute='_compute_totals', store=True,
        help="What the incentive was actually calculated on — differs from Total "
             "Sales whenever a line's product carries its own incentive price.")
    incentive_amount = fields.Monetary(compute='_compute_totals', store=True)
    # The doors opened this month, and what they were worth. Stored on the sheet
    # rather than recomputed on read, so an approved sheet keeps the figure it was
    # approved on even if a clinic is later moved to another route.
    # (client, 2026-08-27)
    new_clinic_count = fields.Integer('New Clinics', readonly=True, copy=False)
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
                 'line_ids.incentive_amount', 'new_clinic_amount')
    def _compute_totals(self):
        for sheet in self:
            sheet.total_sales = sum(sheet.line_ids.mapped('sale_amount'))
            sheet.total_base = sum(sheet.line_ids.mapped('incentive_base'))
            # The clinic bonus rides on top of the slabs rather than into the base:
            # putting it into the base would let opening a door push the executive up a
            # sales slab, which is not what a sales slab measures.
            sheet.incentive_amount = (sum(sheet.line_ids.mapped('incentive_amount'))
                                      + (sheet.new_clinic_amount or 0.0))

    def _new_clinics(self):
        """Clinics this executive registered on their own route, that month.

        Counted from `create_uid` and the clinic's route together: the person who
        entered it, on a round that is theirs. Either alone is wrong — an office user
        entering a clinic for a route is not the executive's door, and a clinic that
        moved onto their route later was not opened by them.
        """
        self.ensure_one()
        start, end = self._month_window(self.period)
        routes = self.executive_id.fw_route_ids
        if not routes:
            return self.env['res.partner']
        return self.env['res.partner'].sudo().search([
            ('create_uid', '=', self.executive_id.id),
            ('team_id', 'in', routes.ids),
            ('create_date', '>=', start),
            ('create_date', '<', end),
        ])

    def action_view_new_clinics(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('New clinics this month'),
            'res_model': 'res.partner', 'view_mode': 'list,form',
            'domain': [('id', 'in', self._new_clinics().ids)],
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
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_incentive.basis', 'delivered')

    @api.model
    def _shipped(self, orders):
        """The orders whose goods actually left: an outgoing transfer done and none
        still open.

        Not lab.delivery's state: every one of its 18,007 rows on this database is
        `draft`, shipped or not, so a basis read off it paid nobody anything. Same
        rule as lab.delivery._live_ids - "has a done picking" alone would call the
        second appliance of a two-line case delivered. Elevated because a manager
        computing sheets need not hold inventory rights. (2026-09-15)
        """
        if not orders:
            return orders
        done, still_open = set(), set()
        for order, state in self.env['stock.picking'].sudo()._read_group(
                [('sale_id', 'in', orders.ids), ('state', '!=', 'cancel'),
                 ('picking_type_code', '=', 'outgoing')],
                ['sale_id', 'state']):
            (done if state == 'done' else still_open).add(order.id)
        return orders.filtered(lambda o: o.id in done and o.id not in still_open)

    @api.model
    def _paid(self, orders, company):
        """The orders whose posted invoices the clinic's receipts have fully reached.

        `payment_state` never reaches 'paid' on this ledger - receipts are plain
        journal entries that are never reconciled - so the Collections countback is
        the only honest answer to "is this paid".
        """
        invoices = orders.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
        if not invoices:
            return orders.browse()
        # The countback is raw SQL: what this transaction posted must be written first.
        self.env['account.move'].flush_model()
        self.env['account.move.line'].flush_model()
        open_moves = {d['move_id'] for d in self.env['lab.collection.performance']
                      .sudo()._open_debits(
                          company, partner_ids=invoices.commercial_partner_id.ids)}
        return orders.filtered(lambda o: any(
            m.state == 'posted' and m.move_type == 'out_invoice'
            for m in o.invoice_ids) and not any(
            m.id in open_moves for m in o.invoice_ids))

    def _qualifying_orders(self):
        """Every order this executive's incentive is judged on for the month.

        Basis is a setting because a lab paying on `delivered` (work that actually
        reached the doctor) is a different promise than one paying on `confirmed`
        (booked) — and which one is true has to be decided once, not line by line.
        """
        self.ensure_one()
        start, end = self._month_window(self.period)
        domain = [
            ('visit_id.user_id', '=', self.executive_id.id),
            ('date_order', '>=', start), ('date_order', '<', end),
            # A sheet is one company's promise: without this, company 2's orders
            # were paid under company 1's slabs.
            ('company_id', '=', self.company_id.id),
            ('state', 'not in', EXCLUDED_STATES),
            ('is_rework', '=', False),
        ]
        basis = self._incentive_basis()
        if basis in ('invoiced', 'paid'):
            domain += [('invoice_ids.state', '=', 'posted')]
        orders = self.env['sale.order'].search(domain)
        if basis == 'paid':
            orders = self._paid(orders, self.company_id)
        elif basis == 'delivered':
            # Outgoing transfers only: a pickup brings an impression IN, and money
            # must not follow that. (2026-08-24)
            orders = self._shipped(orders)
        return orders

    def action_compute(self):
        for sheet in self:
            if sheet.state == 'paid':
                raise UserError(_("%s is already paid — it cannot be recomputed.",
                                  sheet.name))
            sheet.line_ids.unlink()
            orders = sheet._qualifying_orders()
            lines = []
            for order in orders:
                for line in order.order_line.filtered(
                        lambda l: not l.display_type and l.product_uom_qty):
                    override = line.product_id.incentive_price
                    base = (override * line.product_uom_qty) if override \
                        else line.price_subtotal
                    lines.append((0, 0, {
                        'sale_order_id': order.id,
                        'product_id': line.product_id.id,
                        'sale_amount': line.price_subtotal,
                        'incentive_base': base,
                        'used_incentive_price': bool(override),
                    }))
            rule = self.env['lab.incentive.rule']._active_rule(
                sheet.company_id, sheet.period)
            clinics = sheet._new_clinics()
            bonus = len(clinics) * (rule.new_clinic_bonus or 0.0) if rule else 0.0
            sheet.write({'line_ids': lines, 'state': 'computed', 'rule_id': rule.id,
                         'new_clinic_count': len(clinics),
                         'new_clinic_amount': bonus})
            if not rule:
                sheet.message_post(body=_(
                    "No incentive rule is active for %s — the incentive amount is "
                    "zero until one is configured.", sheet.period.strftime('%B %Y')))
                continue
            base_total = sum(sheet.line_ids.mapped('incentive_base'))
            amount = rule.compute_incentive(base_total)
            # Spread proportionally so each line shows its own contribution — useful
            # for the executive to see, meaningless to change by hand.
            for sheet_line in sheet.line_ids:
                share = (sheet_line.incentive_base / base_total) if base_total else 0
                sheet_line.incentive_amount = round(amount * share, 2)
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
        if self.env['ir.config_parameter'].sudo().get_param(
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
    product_id = fields.Many2one('product.product', required=True)
    sale_amount = fields.Monetary(currency_field='currency_id')
    incentive_base = fields.Monetary(
        currency_field='currency_id',
        help="What the incentive was actually computed on for this line.")
    used_incentive_price = fields.Boolean(
        help="This product carries its own incentive price, so the base above is not "
             "the same figure the doctor was charged.")
    incentive_amount = fields.Monetary(
        currency_field='currency_id', readonly=True,
        help="This line's proportional share of the sheet's slab-based total — a "
             "breakdown for transparency, not something to edit by hand.")
    currency_id = fields.Many2one(related='sheet_id.currency_id')

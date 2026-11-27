# -*- coding: utf-8 -*-
"""Three kinds of case, and the order each one makes.

New work is the order as it always was. A REWORK is the lab remaking an earlier
job (an order with Rework ticked: RE numbers, no charge). A TRIAL is a try-in
sent to the clinic before the work is finished - a wax trial, a jig - also free,
numbered on its own TR sequence so it is never confused with the work it leads to.

Until now only the office could raise either: the executive entered the case as
new work and somebody turned it into a rework afterwards, or did not. The case
says which it is now, and the order it makes is that kind from the start.
(client, 2026-11-27: "In the case entry able to enter the Rework SO with new entry
sequence. Also another type of SO is needed: Trial SOs. Trial also don't have unit
price, it should be zero. Use another sequence for trial SOs. Executive can enter
the Trial and Rework SOs.")
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CASE_KINDS = [('new', 'New Work'), ('rework', 'Rework'), ('trial', 'Trial')]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_trial = fields.Boolean(
        'Trial', copy=False, tracking=True,
        help="A try-in sent to the clinic before the work is finished. Not charged, "
             "and numbered on its own TR sequence.")

    @api.constrains('is_trial', 'is_rework')
    def _check_trial_or_rework(self):
        if any(order.is_trial and order.is_rework for order in self):
            raise ValidationError(_("An order is a rework or a trial, not both."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            trial = vals.get('is_trial') or self.env.context.get('default_is_trial')
            if trial and vals.get('name', _('New')) == _('New'):
                Seq = self.env['ir.sequence']
                if vals.get('company_id'):
                    Seq = Seq.with_company(vals['company_id'])
                vals['name'] = Seq.next_by_code('sale.trial') or _('New')
        return super().create(vals_list)

    def write(self, vals):
        # Ticking Trial on an order moves it onto the TR numbers, as Rework moves it
        # onto RE; unticking puts the number it had back.
        flip_on = vals.get('is_trial') and any(not o.is_trial for o in self)
        flip_off = 'is_trial' in vals and not vals['is_trial'] \
            and any(o.is_trial for o in self)
        res = super().write(vals)
        if flip_on:
            Seq = self.env['ir.sequence']
            for order in self.filtered(lambda o: o.is_trial):
                if (order.name or '').startswith('TR') or not order.name:
                    continue
                new_name = Seq.with_company(order.company_id).next_by_code('sale.trial')
                if new_name:
                    order._lab_rename(order.name, new_name, keep_prev=True)
        elif flip_off:
            self.filtered(lambda o: not o.is_trial and o.rework_prev_name) \
                ._lab_restore_number()
        return res


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # A trial is not charged, the same rule a rework has: price zero on screen, on
    # save and on the invoice.
    @api.depends('order_id.is_trial')
    def _compute_price_unit(self):
        super()._compute_price_unit()
        for line in self:
            if line.order_id.is_trial and not line.display_type:
                line.price_unit = 0.0

    def _lab_zero_rework_prices(self):
        super()._lab_zero_rework_prices()
        charged = self.filtered(
            lambda l: l.order_id.is_trial and not l.display_type and l.price_unit)
        if charged:
            # writing price 0 comes back here once and finds nothing left to zero
            charged.write({'price_unit': 0.0})

    def _prepare_invoice_line(self, **optional_values):
        vals = super()._prepare_invoice_line(**optional_values)
        if self.order_id.is_trial and not self.display_type:
            vals['price_unit'] = 0.0
        return vals


class LabCase(models.Model):
    _inherit = 'lab.case'

    case_kind = fields.Selection(
        CASE_KINDS, string='Type of Case', default='new', required=True, tracking=True,
        help="New work, a rework of an earlier job (not charged, RE number) or a trial "
             "sent for try-in (not charged, TR number).")
    rework_reason_id = fields.Many2one(
        'lab.rework.reason', string='Rework Reason',
        help="Why the work is being made again.")
    rework_origin_id = fields.Many2one(
        'sale.order', string='Original Order',
        domain="[('is_rework', '=', False), ('partner_id', '=?', partner_id)]",
        help="The earlier order being remade, if you know it. Optional.")
    rework_note = fields.Char('What Came Back')

    @api.onchange('case_kind')
    def _onchange_case_kind(self):
        if self.case_kind != 'rework':
            self.rework_reason_id = False
            self.rework_origin_id = False
            self.rework_note = False

    def _check_ready(self):
        super()._check_ready()
        for case in self:
            if case.case_kind == 'rework' and not case.rework_reason_id:
                raise UserError(_(
                    "%s is a rework: say why it is being made again (Rework Reason).",
                    case.patient))

    def _order_values(self):
        vals = super()._order_values()
        kind = self.case_kind
        vals['is_rework'] = kind == 'rework'
        vals['is_trial'] = kind == 'trial'
        if kind == 'rework':
            reason = self.rework_reason_id
            vals.update({
                'rework_reason_id': reason.id,
                'rework_responsibility': reason.responsibility_default or False,
                'rework_origin_id': self.sudo().rework_origin_id.id or False,
                'rework_note': self.rework_note or False,
            })
        return vals

    # A rework or a trial IS the same patient again: no "same patient recently"
    # notice on the case, and none on the order.
    def _compute_duplicates(self):
        super()._compute_duplicates()
        for case in self.filtered(lambda c: c.case_kind in ('rework', 'trial')):
            case.duplicate_warning = False

    def _repeat_note(self, order):
        if self.case_kind in ('rework', 'trial'):
            return False
        return super()._repeat_note(order)

# -*- coding: utf-8 -*-
"""A rework is just an order that remakes an earlier one.

There is no separate rework document any more: tick **Rework** on the order, say why
(Reason), and - if you know it - point at the **Original Order**. Choosing the original
copies its clinic, patient, clinical details and work lines at zero price, so the counter
does not retype them. Everything downstream (production, delivery, tracking) already works
on sale orders, which is why the intermediate record was only ever a detour.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

RESPONSIBILITY = [
    ('lab_error', 'Lab Error'), ('fit_issue', 'Fit Issue'),
    ('transit_damage', 'Transit Damage'), ('doctor_change', 'Doctor Change'),
    ('patient_issue', 'Patient Issue'), ('unknown', 'Unknown'),
]
# order fields copied from the original when one is picked
CARRY_OVER = (
    'patient', 'age', 'gender', 'send_through', 'modification', 'instruction',
    'is_screw', 'is_bite', 'is_bands', 'is_wires', 'is_teeth', 'is_facebow',
    'is_others', 'is_3d_model_print', 'impression_type', 'scanned_impression',
    'impression_tray', 'wax_bite', 'user_id', 'team_id', 'partner_id',
    # The kind of appliance: a remake of a fixed appliance is a fixed appliance,
    # and the field is required in draft - without it, picking the original left
    # the counter with a form it could not save. (client, 2026-09-10)
    'appliance_type',
)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    rework_origin_id = fields.Many2one(
        'sale.order', string='Original Order', copy=False, index=True, ondelete='restrict',
        # `=?` drops the leaf when no customer is chosen yet, so the field still offers
        # every order at that point and narrows to the clinic's own jobs once one is set.
        # No `('id','!=',id)` leaf: on an unsaved order `id` is not in the client's
        # evaluation context, the whole domain then fails to evaluate and EVERY order is
        # offered - which is exactly what it did. Self-reference is caught by a constraint.
        domain="[('is_rework', '=', False), ('partner_id', '=?', partner_id)]",
        help="The job being remade, from this customer's own orders. Optional - a rework "
             "can be registered without it - but picking it fills the clinic, patient and "
             "work lines in for you.")
    rework_reason_id = fields.Many2one(
        'lab.rework.reason', string='Rework Reason', copy=False, index=True,
        help="Why the work is being remade. Reported in Sales > Reporting.")
    rework_responsibility = fields.Selection(
        RESPONSIBILITY, string='Responsibility', copy=False,
        help="Who the remake is down to. Pre-filled from the reason.")
    rework_note = fields.Char(string='Rework Note', copy=False)
    rework_ids = fields.One2many('sale.order', 'rework_origin_id', string='Reworks')
    rework_count = fields.Integer(compute='_compute_rework_count')

    @api.depends('rework_ids')
    def _compute_rework_count(self):
        counts = dict(self.env['sale.order']._read_group(
            [('rework_origin_id', 'in', self.ids)], ['rework_origin_id'], ['__count']))
        for order in self:
            order.rework_count = counts.get(order, 0)

    # ------------------------------------------------------------------ behaviour
    @api.onchange('is_rework')
    def _onchange_is_rework(self):
        if not self.is_rework:
            self.rework_origin_id = False
            self.rework_reason_id = False
            self.rework_responsibility = False

    @api.onchange('partner_id')
    def _onchange_partner_rework_origin(self):
        """Changing the clinic drops an original order that belongs to another one."""
        if self.rework_origin_id and self.partner_id and \
                self.rework_origin_id.partner_id != self.partner_id:
            self.rework_origin_id = False

    @api.onchange('rework_reason_id')
    def _onchange_rework_reason_id(self):
        if self.rework_reason_id and not self.rework_responsibility:
            self.rework_responsibility = self.rework_reason_id.responsibility_default

    @api.onchange('rework_origin_id')
    def _onchange_rework_origin_id(self):
        """Copy the original job onto this one: same clinic, same patient, same work."""
        origin = self.rework_origin_id
        if not origin:
            return
        self.is_rework = True
        for field in CARRY_OVER:
            if field in self._fields and origin[field]:
                self[field] = origin[field]
        lines = origin.order_line.filtered(
            lambda l: not l.display_type and not l.is_urgent_service)
        self.order_line = [(5, 0, 0)] + [(0, 0, self._rework_line_vals(l)) for l in lines]

    @api.model
    def _rework_line_vals(self, line):
        """A remade line: same work, same arch and colour - never charged."""
        vals = {
            'product_id': line.product_id.id,
            'name': line.name,
            'product_uom_qty': line.product_uom_qty,
            'product_uom_id': line.product_uom_id.id,
            'price_unit': 0.0,
        }
        for field in ('ul', 'color_scheme'):
            if field in line._fields and line[field]:
                vals[field] = line[field].id if field == 'color_scheme' else line[field]
        return vals

    @api.constrains('rework_origin_id')
    def _check_rework_origin(self):
        for order in self:
            if order.rework_origin_id == order:
                raise ValidationError(_("An order cannot be a rework of itself."))
            if order.rework_origin_id and order.rework_origin_id.is_rework:
                raise ValidationError(_(
                    "%s is itself a rework: point at the original job instead.",
                    order.rework_origin_id.name))

    # NOTE: the reason is required by the FORM (required="is_rework"), not by a Python
    # constraint. The 2,454 reworks migrated from Odoo 10 have no reason on file, and a
    # server-side rule would refuse every later edit of them - a trap for the counter with
    # nothing gained: nobody can save a new rework without a reason anyway.

    # -- Number follows the kind (client, 2026-08-20) -------------------------------
    # Ticking Rework on an existing order - draft or confirmed - moves it onto the RE
    # sequence, the same numbering a rework gets at creation. The order's own number is
    # kept aside and put back if the tick was a mistake, and the documents already cut
    # from the order (deliveries, invoices, productions) are re-pointed so their Source
    # keeps matching the order they came from.
    rework_prev_name = fields.Char(copy=False, readonly=True,
        help="The order's number before it was marked as a rework.")

    def write(self, vals):
        flip_on = vals.get('is_rework') and any(not o.is_rework for o in self)
        flip_off = 'is_rework' in vals and not vals['is_rework'] \
            and any(o.is_rework for o in self)
        res = super().write(vals)
        if flip_on:
            self.filtered(lambda o: o.is_rework)._lab_renumber_as_rework()
        elif flip_off:
            self.filtered(lambda o: not o.is_rework and o.rework_prev_name) \
                ._lab_restore_number()
        return res

    def _lab_renumber_as_rework(self):
        Seq = self.env['ir.sequence']
        for order in self:
            if (order.name or '').startswith('RE') or not order.name:
                continue                     # already carries a rework number
            new_name = Seq.with_company(order.company_id).next_by_code('sale.rework')
            if not new_name:
                continue
            order._lab_rename(order.name, new_name, keep_prev=True)

    def _lab_restore_number(self):
        for order in self:
            prev = order.rework_prev_name
            if prev and not self.search_count([('name', '=', prev), ('id', '!=', order.id)]):
                order._lab_rename(order.name, prev, keep_prev=False)

    def _lab_rename(self, old_name, new_name, keep_prev):
        """Rename the order and re-point the documents that carry its number as text."""
        self.ensure_one()
        self.write({'name': new_name,
                    'rework_prev_name': old_name if keep_prev else False})
        pickings = self.env['stock.picking'].sudo().search([('origin', '=', old_name)])
        pickings.write({'origin': new_name})
        self.sudo().invoice_ids.filtered(
            lambda m: m.invoice_origin == old_name).write({'invoice_origin': new_name})
        if 'mrp.production' in self.env:
            self.env['mrp.production'].sudo().search(
                [('origin', '=', old_name)]).write({'origin': new_name})
        self.message_post(body=_('Renumbered %(old)s → %(new)s (rework).',
                                 old=old_name, new=new_name))

    def action_create_rework(self):
        """Open a new order pre-filled as a rework of this one."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('New Rework'),
            'res_model': 'sale.order', 'view_mode': 'form',
            'context': {'default_is_rework': True, 'default_rework_origin_id': self.id,
                        'default_partner_id': self.partner_id.id},
        }

    def action_view_reworks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Reworks'),
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('rework_origin_id', '=', self.id)],
            'context': {'default_is_rework': True, 'default_rework_origin_id': self.id},
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # A rework is work the lab redoes at its own cost: the clinic is never charged for it.
    # Copied lines already came over at zero (_rework_line_vals), but a line ADDED to the
    # rework by hand used to take the pricelist price, and so did the invoice made from it.
    # Price zero is enforced here for every line of a rework order, on screen and on save.
    # (client, 2026-08-18)
    @api.depends('order_id.is_rework')
    def _compute_price_unit(self):
        super()._compute_price_unit()
        for line in self:
            if line.order_id.is_rework and not line.display_type:
                line.price_unit = 0.0

    def _lab_zero_rework_prices(self):
        charged = self.filtered(
            lambda l: l.order_id.is_rework and not l.display_type and l.price_unit)
        if charged:
            super(SaleOrderLine, charged).write({'price_unit': 0.0})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._lab_zero_rework_prices()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'price_unit' in vals or 'order_id' in vals:
            self._lab_zero_rework_prices()
        return res

    def _prepare_invoice_line(self, **optional_values):
        """The invoice of a rework carries the same zero price as the order."""
        vals = super()._prepare_invoice_line(**optional_values)
        if self.order_id.is_rework and not self.display_type:
            vals['price_unit'] = 0.0
        return vals

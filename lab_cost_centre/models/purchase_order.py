# -*- coding: utf-8 -*-
"""A purchase, and the department that asked for it.

One direction, as on the sale side: the line derives from the product, the
header from its lines. See sale_order.py for why the reverse cannot exist.
"""
from odoo import api, models


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """Whoever asked, else the store that will receive it.

        A purchase raised for a department's material belongs to that
        department - that is the whole point of a material request. One raised
        by the store on its own belongs to the store, and the cost moves to a
        bench later, when the material is issued to it.
        """
        self.ensure_one()
        for line in self.order_line:
            if line.department_id:
                return line.department_id
        return self.picking_type_id.default_location_dest_id.department_id

    @api.depends('order_line.department_id', 'picking_type_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    @api.onchange('department_id')
    def _onchange_department_fill_lines(self):
        for order in self:
            if not order.department_id:
                continue
            for line in order.order_line:
                if not line.department_id:
                    line.department_id = order.department_id


class PurchaseOrderLine(models.Model):
    _name = 'purchase.order.line'
    _inherit = ['purchase.order.line', 'lab.cost.centre.mixin']

    def _derive_department(self):
        self.ensure_one()
        if self.product_id:
            return self.product_id.categ_id._lab_department()
        return self.env['hr.department']

    @api.depends('product_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._lab_apply_analytic()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'department_id' in vals or 'product_id' in vals:
            self._lab_apply_analytic()
        return res

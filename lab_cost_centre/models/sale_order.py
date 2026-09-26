# -*- coding: utf-8 -*-
"""A case, and the department that will make it.

ONE DIRECTION ONLY. A line takes its department from what it sells; the header
takes its from its lines. The reverse - a line falling back to its header - reads
as the obvious courtesy and is a recursion: the header computes from the lines,
each line computes from the header, and Odoo unwinds the stack. A header chosen
by hand still reaches its lines, through the onchange below, which runs once and
asks nothing back.
"""
from odoo import api, models


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """The department of the work, not of the salesperson.

        An order is one case, made by one bench more often than not, so the
        department of its first line is the honest answer. An order that mixes a
        crown and a retainer keeps the right department on each line, and the
        header only says where the bulk of it goes.
        """
        self.ensure_one()
        for line in self.order_line:
            if line.department_id:
                return line.department_id
        return self.env['hr.department']

    @api.depends('order_line.department_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    @api.onchange('department_id')
    def _onchange_department_fill_lines(self):
        """A department chosen on the order reaches the lines that have none."""
        for order in self:
            if not order.department_id:
                continue
            for line in order.order_line:
                if not line.department_id:
                    line.department_id = order.department_id


class SaleOrderLine(models.Model):
    _name = 'sale.order.line'
    _inherit = ['sale.order.line', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """From the product's category.

        The lab's categories already ARE its departments - CERAMIC, ACRYLIC,
        ZIRCONIA, ORTHODONTIC - which is why nobody has to pick a department
        when registering a case.
        """
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

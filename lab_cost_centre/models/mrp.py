# -*- coding: utf-8 -*-
"""A job on the floor, and the bench that is paying for it."""
from odoo import api, fields, models


class MrpProduction(models.Model):
    _name = 'mrp.production'
    _inherit = ['mrp.production', 'lab.cost.centre.mixin']

    # `precompute=False`, against the mixin's default. Precompute runs a
    # stored compute during create, before the record exists - and this one
    # reads `workorder_ids`, which is written after. Odoo notices, warns on
    # every registry load, and disables it anyway; saying so here makes the
    # behaviour deliberate and stops the warning. The department is still
    # computed on create, just after the relation is there.
    department_id = fields.Many2one(precompute=False)

    def _derive_department(self):
        """The bench the job starts at, else what the product is made of.

        A case routed Wax Up, CAD/CAM, Metal, Ceramic belongs to all four in
        turn, and its work orders say so one by one. The manufacturing order
        itself is filed under where it begins, which is how the floor talks
        about it: "that one is still in Wax Up".
        """
        self.ensure_one()
        first = self.workorder_ids[:1].workcenter_id.department_id
        if first:
            return first
        operations = self.bom_id.operation_ids[:1].workcenter_id.department_id
        if operations:
            return operations
        if self.product_id:
            return self.product_id.categ_id._lab_department()
        return self.env['hr.department']

    @api.depends('workorder_ids.workcenter_id', 'bom_id', 'product_id')
    def _compute_department_id(self):
        return super()._compute_department_id()


class MrpWorkorder(models.Model):
    _name = 'mrp.workorder'
    _inherit = ['mrp.workorder', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """The bench, always.

        This is the one document where the department is a fact rather than a
        derivation: the operation happens at a work centre, and the work centre
        belongs to a department. It is also what makes cost per stage real - the
        same case contributes minutes to four different cost centres.
        """
        self.ensure_one()
        if self.workcenter_id.department_id:
            return self.workcenter_id.department_id
        # The job's own department, read through the bench rather than through
        # the job's computed field: a work order asking its production, which
        # computes itself from its work orders, is a loop the ORM unwinds by
        # running out of stack. (see sale_order.py)
        return self.production_id.bom_id.operation_ids[:1].workcenter_id.department_id

    @api.depends('workcenter_id', 'production_id.bom_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

# -*- coding: utf-8 -*-
"""The three masters every document reads its department from.

A bench belongs to a department, a store belongs to a department, and a product
category is made by one. Those three facts are all it takes to stamp the right
department on a sale line, a purchase line, a transfer and a job without anybody
choosing it by hand - which is the only way a field like this stays true.
"""
from odoo import api, fields, models


class MrpWorkcenter(models.Model):
    _inherit = 'mrp.workcenter'

    department_id = fields.Many2one(
        'hr.department', string='Department', index=True,
        help="The department that runs this bench. A job at this bench is that "
             "department's cost.")
    cost_centre_id = fields.Many2one(
        related='department_id.cost_centre_id', store=True, string='Cost Centre')

    @api.onchange('department_id')
    def _onchange_department_analytic(self):
        """Point the bench's own analytic distribution at its cost centre.

        Work centres already carry a distribution, which core uses to post the
        cost of the time worked. Left empty, that cost goes nowhere; filled by
        hand, it drifts from the department. Filled from the department, the two
        can never disagree.
        """
        for workcenter in self:
            centre = workcenter.department_id.cost_centre_id
            if centre and not workcenter.analytic_distribution:
                workcenter.analytic_distribution = {str(centre.id): 100.0}


class StockLocation(models.Model):
    _inherit = 'stock.location'

    department_id = fields.Many2one(
        'hr.department', string='Department', index=True,
        help="The department this store belongs to. Material moving into it is "
             "that department's, and what leaves it is what the department used.")


class ProductCategory(models.Model):
    _inherit = 'product.category'

    department_id = fields.Many2one(
        'hr.department', string='Made By', index=True,
        help="The department that makes or buys this kind of product. A sale "
             "line, a job and a purchase line all take their department from "
             "here when nothing more specific says otherwise.")

    def _lab_department(self):
        """This category's department, or the nearest parent's.

        Categories are a tree here - CERAMIC sits under nothing, but a lab that
        later splits it into CERAMIC / ZIRCONIA should not have to set the
        department twice.

        Empty in, empty out. A product with no category is not a question this
        can answer, and `ensure_one()` on nothing raises - from inside a STORED
        compute, which means every flush of a job whose product has no category
        died with "Expected singleton: product.category()". A resolver returns
        what it found; it does not decide that finding nothing is a crash.
        """
        if not self:
            return self.env['hr.department']
        self.ensure_one()
        category = self
        while category:
            if category.department_id:
                return category.department_id
            category = category.parent_id
        return self.env['hr.department']

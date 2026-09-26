# -*- coding: utf-8 -*-
"""Cost centres, which are analytic accounts wearing the lab's own name."""
from odoo import api, fields, models


class AccountAnalyticPlan(models.Model):
    _inherit = 'account.analytic.plan'

    is_cost_centre_plan = fields.Boolean(
        string='Cost Centres', copy=False,
        help="The plan that holds one account per department. Exactly one plan "
             "should carry this, and it is what the rest of the lab writes to.")

    @api.model
    def _lab_cost_centre_plan(self):
        """The plan the departments' cost centres live in.

        Found by the flag rather than by an xml id, so a lab that renames or
        rebuilds its plan keeps working; created if it is missing, because every
        path that writes a cost centre needs one to exist.
        """
        plan = self.search([('is_cost_centre_plan', '=', True)], limit=1)
        if plan:
            return plan
        return self.create({
            'name': 'Cost Centres',
            'is_cost_centre_plan': True,
            'default_applicability': 'optional',
        })


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    department_id = fields.One2many(
        'hr.department', 'cost_centre_id', string='Department')
    department_code = fields.Char(
        compute='_compute_department_code', string='Department Code', store=True)

    @api.depends('department_id.code')
    def _compute_department_code(self):
        for account in self:
            account.department_code = account.department_id[:1].code or ''

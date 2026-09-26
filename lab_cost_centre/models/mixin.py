# -*- coding: utf-8 -*-
"""What every document that costs or earns money carries.

Two fields and one rule: a department, derived from whatever the document
already knows, and the cost centre that department posts to - never chosen
twice, never overwritten once somebody has chosen it by hand.
"""
from odoo import api, fields, models


class LabCostCentreMixin(models.AbstractModel):
    _name = 'lab.cost.centre.mixin'
    _description = 'Department and Cost Centre'

    department_id = fields.Many2one(
        'hr.department', string='Department', index=True,
        compute='_compute_department_id', store=True, readonly=False,
        precompute=True,
        help="Who this belongs to. Derived from what the document already knows "
             "and editable when the derivation is wrong.")
    cost_centre_id = fields.Many2one(
        'account.analytic.account', string='Cost Centre', store=True, index=True,
        related='department_id.cost_centre_id',
        help="Where its cost or revenue lands in the ledger.")

    def _derive_department(self):
        """The department this record belongs to. Overridden per model."""
        self.ensure_one()
        return self.env['hr.department']

    @api.depends()
    def _compute_department_id(self):
        """Fill a blank; never argue with a person.

        A stored computed field with `readonly=False` recomputes whenever its
        dependencies change, which would quietly undo somebody's correction the
        next time the line was touched. Skipping records that already carry a
        department is what makes the field both automatic and trustworthy.
        """
        for record in self:
            if record.department_id:
                continue
            record.department_id = record._derive_department()

    # ------------------------------------------------------- analytic plumbing
    def _lab_analytic_distribution(self, existing=None):
        """The distribution to write, or None to leave whatever is there.

        Never replaces a distribution somebody set: an analytic entry is an
        accounting statement, and a module that rewrites one silently is a module
        the accountant stops trusting.
        """
        self.ensure_one()
        if existing:
            return None
        centre = self.cost_centre_id
        if not centre:
            return None
        return {str(centre.id): 100.0}

    def _lab_apply_analytic(self, field='analytic_distribution'):
        """Put the cost centre on the lines that can carry one."""
        for record in self:
            if field not in record._fields:
                continue
            distribution = record._lab_analytic_distribution(record[field])
            if distribution:
                record[field] = distribution

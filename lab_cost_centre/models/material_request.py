# -*- coding: utf-8 -*-
"""A department asking the main store for material.

The request already knows which store it is for; this says which department
that store belongs to, so a month of requests adds up to a consumption figure
per department without anybody tagging anything.
"""
from odoo import api, models


class MaterialRequest(models.Model):
    _name = 'material.request'
    _inherit = ['material.request', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """From the store it is destined for, else the requester's own.

        The store first: a technician who moved bench last month still has the
        old department on their employee record for a while, and the material is
        going where it is going.
        """
        self.ensure_one()
        if self.location_id.department_id:
            return self.location_id.department_id
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.user_id.id)], limit=1)
        return employee.department_id

    @api.depends('location_id', 'user_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

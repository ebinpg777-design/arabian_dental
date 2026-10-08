# -*- coding: utf-8 -*-
"""Which customers a salesperson brought in.

The new-clinic bonus used to count every clinic an executive registered on their own
route. Nobody on the lab's book has a route, so it counted nothing - and registering
a clinic is office work as often as it is the salesperson's. The client's rule
(2026-10-08): a clinic is a new clinic for incentive only when it is marked as a new
lead brought in by its salesperson, the Salesperson named on the customer.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    incentive_new_lead = fields.Boolean(
        string='New Lead by Salesperson', tracking=True, copy=False,
        help="This customer was brought in by its Salesperson: it counts as a new clinic "
             "on that salesperson's incentive sheet for the month it was registered.")

    @api.constrains('incentive_new_lead', 'user_id')
    def _check_new_lead_has_a_salesperson(self):
        for partner in self:
            if partner.incentive_new_lead and not partner.user_id:
                raise ValidationError(_(
                    "%s is marked as a new lead by its salesperson: choose the "
                    "Salesperson who brought it in.", partner.display_name))

# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    use_quotations = fields.Boolean(
        'Sales', default=True,
        help="Check this box to manage sales in this sales team.")

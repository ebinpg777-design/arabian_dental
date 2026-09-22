# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_incentive_basis = fields.Selection(
        [('confirmed', 'Confirmed'), ('delivered', 'Delivered'),
         ('invoiced', 'Invoiced'), ('paid', 'Paid')],
        string='Incentive counted on', default='delivered',
        config_parameter='lab_incentive.basis',
        help="Which of the month's orders count towards an executive's incentive. "
             "'Delivered' pays on work that actually reached the doctor.")
    lab_incentive_generate_cron = fields.Boolean(
        'Generate sheets automatically on the 1st',
        config_parameter='lab_incentive.auto_generate', default=False)

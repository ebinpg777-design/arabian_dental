# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Only billed amounts count (client, 2026-10-08): the choice left is whether the
    # bill must also have been paid.
    lab_incentive_basis = fields.Selection(
        [('invoiced', 'Billed'), ('paid', 'Billed and paid')],
        string='Incentive counted on', default='invoiced',
        config_parameter='lab_incentive.basis',
        help="Billed: what was invoiced in the month, less credit notes. Billed and "
             "paid: only invoices the clinic's receipts have fully reached.")
    lab_incentive_generate_cron = fields.Boolean(
        'Generate sheets automatically on the 1st',
        config_parameter='lab_incentive.auto_generate', default=False)

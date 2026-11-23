# -*- coding: utf-8 -*-
from odoo import fields, models

from .incentive_sheet import INCENTIVE_SHEET_BASES


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Only billed amounts count (client, 2026-10-08): the choice left is whether the
    # bill must also have been paid.
    # Collected (client, 2026-11-23): the money received in the month, a cheque in the
    # month it clears - an uncleared cheque is not a payment.
    lab_incentive_basis = fields.Selection(
        INCENTIVE_SHEET_BASES,
        string='Incentive counted on', default='invoiced',
        config_parameter='lab_incentive.basis',
        help="Billed: what was invoiced in the month, less credit notes. Billed and "
             "paid: only invoices the clinic's receipts have fully reached - a cheque "
             "not yet cleared does not pay. Collected: what the clinics paid in the "
             "month for the executive's invoices, before tax: cash and bank on their "
             "date, a cheque in the month it clears, whatever month it was received.")
    lab_incentive_generate_cron = fields.Boolean(
        'Generate sheets automatically on the 1st',
        config_parameter='lab_incentive.auto_generate', default=False)

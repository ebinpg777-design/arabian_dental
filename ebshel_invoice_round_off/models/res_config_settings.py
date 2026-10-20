# Part of ebshel_invoice_round_off. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    invoice_round_off_id = fields.Many2one(
        related="company_id.invoice_round_off_id", readonly=False
    )
    invoice_round_off_auto = fields.Boolean(
        related="company_id.invoice_round_off_auto", readonly=False
    )

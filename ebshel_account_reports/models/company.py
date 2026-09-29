# -*- coding: utf-8 -*-
"""How a company wants its reports written. Set once, per company; every report,
on the screen and in every export, follows."""
from odoo import fields, models

from .engine import NEGATIVE_STYLES


class ResCompany(models.Model):
    _inherit = 'res.company'

    ebshel_fin_totals_last = fields.Boolean(
        'Totals under their sections',
        help="A section is closed by its total: the heading stands alone, the lines follow, "
             "and 'Total ...' comes last - the way a statement is laid out on paper. "
             "Off, a section carries its total on its own heading.")
    ebshel_fin_negative = fields.Selection(
        NEGATIVE_STYLES, string='Negative amounts', default='minus', required=True,
        help="How an amount below zero is written in the reports: with a minus sign, "
             "in brackets as accountants write it, or with the sign after the figure.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ebshel_fin_totals_last = fields.Boolean(related='company_id.ebshel_fin_totals_last', readonly=False)
    ebshel_fin_negative = fields.Selection(related='company_id.ebshel_fin_negative', readonly=False, required=True)

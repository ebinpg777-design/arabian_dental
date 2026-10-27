# -*- coding: utf-8 -*-
"""How a company wants its reports written. Set once, per company; every report,
on the screen and in every export, follows."""
from odoo import api, fields, models

from .engine import DATE_FORMATS, DEFAULT_DATE_FORMAT, NEGATIVE_STYLES


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
    ebshel_fin_date_format = fields.Selection(
        DATE_FORMATS, string='Date format', required=True,
        default=lambda self: self._default_fin_date_format(),
        help="How a date is written in the accounting reports - on the screen, in the PDF "
             "and in the workbook - whatever language each reader uses.")

    @api.model
    def _default_fin_date_format(self):
        """The format of the language the company is set up in, when it is one of the
        choices: a company keeps, on the day this arrives, the dates it already saw."""
        lang = self.env['res.lang']._get_data(code=self.env.lang or 'en_US')
        pattern = lang and lang.date_format
        return pattern if pattern in dict(DATE_FORMATS) else DEFAULT_DATE_FORMAT

    def ebshel_fin_format_date(self, day):
        """A date the way this company writes dates in its reports. Public, so a PDF
        template can call it: `o.company_id.ebshel_fin_format_date(line.date)`."""
        return self.env['ebshel.fin.engine'].fmt_date(day, self[:1] or None)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ebshel_fin_totals_last = fields.Boolean(related='company_id.ebshel_fin_totals_last', readonly=False)
    ebshel_fin_negative = fields.Selection(related='company_id.ebshel_fin_negative', readonly=False, required=True)
    ebshel_fin_date_format = fields.Selection(related='company_id.ebshel_fin_date_format', readonly=False,
                                              required=True)

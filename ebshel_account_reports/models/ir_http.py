# -*- coding: utf-8 -*-
"""Each company's date format for the accounting screens in the browser."""
from odoo import models

from .engine import luxon_date_format


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        user = self.env.user
        if user._is_internal():
            # every company the user can switch to: the screens pick the active one
            info['ebshel_fin_date_formats'] = {
                company.id: luxon_date_format(company.ebshel_fin_date_format)
                for company in user.company_ids.sudo()
            }
        return info

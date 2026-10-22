# -*- coding: utf-8 -*-
from odoo import api, models


class LabCeoDashboard(models.AbstractModel):
    _inherit = 'lab.ceo.dashboard'

    @api.model
    def _cash(self):
        """The field force's cash, and the petty cash funds the office holds."""
        res = super()._cash()
        funds = self.env['ebshel.petty.cash.fund'].sudo().search(
            [('state', '=', 'open'), ('company_id', 'in', self.env.companies.ids)])
        res.update({
            'petty_funds': round(sum(funds.mapped('balance')), 2),
            'petty_fund_count': len(funds),
        })
        return res

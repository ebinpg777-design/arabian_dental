# -*- coding: utf-8 -*-
"""Invoice lines that come from a rework are never charged.

`sale.order.line._prepare_invoice_line` already sets zero when the invoice is created
from the order, but an invoice can be edited afterwards (or a line re-linked to a rework
order), so the rule is enforced on the invoice line itself as well.
"""
from odoo import api, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _lab_rework_lines(self):
        if 'sale_line_ids' not in self._fields:
            return self.browse()
        # NB: on an invoice line display_type is 'product' for a real line (it is False
        # only on sale.order.line), so test for the section/note types instead.
        return self.filtered(
            lambda l: l.display_type not in ('line_section', 'line_note')
            and any(sol.order_id.is_rework for sol in l.sale_line_ids)
            and l.price_unit)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        charged = lines._lab_rework_lines()
        if charged:
            super(AccountMoveLine, charged).write({'price_unit': 0.0})
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'price_unit' in vals or 'sale_line_ids' in vals:
            charged = self._lab_rework_lines()
            if charged:
                super(AccountMoveLine, charged).write({'price_unit': 0.0})
        return res

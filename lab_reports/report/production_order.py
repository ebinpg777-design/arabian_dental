# -*- coding: utf-8 -*-
"""Production Order: the sheet the counter actually prints.

This is the long-standing report bound to the sales order, and it used to lay out one
work item per full-width block with every label on its own line - three to a page, and
the first one starting a third of the way down because it inherited the standard
header spacing. Printing a day's orders meant a stack of paper.

It now renders the same eight-to-a-sheet grid as the production slips, which is the
layout that had already been fought through wkhtmltopdf's habit of collapsing declared
heights. One report, one button, eight slips. (client, 2026-08-28)
"""
from odoo import api, models

from odoo.addons.sale_custom.report.production_slip import _pages, slips_for_orders


class ReportProductionOrder(models.AbstractModel):
    _name = 'report.lab_reports.report_mrporder'
    _description = 'Production Order Slips'

    @api.model
    def _get_report_values(self, docids, data=None):
        orders = self.env['sale.order'].browse(docids)
        slips = slips_for_orders(orders)
        return {
            'doc_ids': docids,
            'doc_model': 'sale.order',
            'docs': orders,
            'slips': slips,
            'pages': _pages(slips),
        }

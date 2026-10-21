# -*- coding: utf-8 -*-
from odoo import models


class SalespersonSummaryWizard(models.TransientModel):
    """The Salesperson Summary on the lab's letterhead (client, 2026-10-21: the v17
    "Sales Person Summary Report", redesigned). The letterhead is drawn in the page
    body, as on the dispatch register; the layout gives the footer bar."""
    _inherit = 'salesperson.summary.wizard'

    def _layout(self):
        return 'sale_custom.external_layout_lab'

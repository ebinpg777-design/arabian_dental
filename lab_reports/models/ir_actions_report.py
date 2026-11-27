# -*- coding: utf-8 -*-
from odoo import models

# The purchase print-outs drawn on the invoice's stationery: the letterhead is in
# the body, so they need the invoice's paper format (no header band, 6 mm top).
LAB_LETTERHEAD_REPORTS = (
    'purchase.report_purchaseorder',
    'purchase.report_purchasequotation',
    'ebshel_purchase_request.report_rfq_document',
)


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        """The invoice's paper format for the purchase print-outs, unless someone
        chose one for them. Done here rather than by writing the actions' paper
        format from data: an update of `purchase` or of the store module would put
        theirs back, and the letterhead would print under a 52 mm empty band."""
        if self.report_name in LAB_LETTERHEAD_REPORTS:
            shipped = self.env.ref('base.paperformat_euro', raise_if_not_found=False)
            if not self.paperformat_id or self.paperformat_id == shipped:
                lab = self.env.ref('sale_custom.paperformat_lab_invoice', raise_if_not_found=False)
                if lab:
                    return lab
        return super().get_paperformat()

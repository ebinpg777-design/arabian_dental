# -*- coding: utf-8 -*-
from odoo import api, models


class EinvoiceReportWizard(models.TransientModel):
    """The lab's e-invoice report names the buyer by the clinic's billing name, as the
    Odoo 17 report did (client, 2026-10-21), and by its registered name without one."""
    _inherit = 'einvoice.report.wizard'

    @api.model
    def _buyer_name(self, move):
        partner = move.partner_id
        return (partner.billing_name or partner.commercial_partner_id.billing_name
                or super()._buyer_name(move))

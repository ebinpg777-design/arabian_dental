# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, _

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Restricted like purchase's own purchase_order_count: the count is read from
    # quotations the user must be allowed to see, so for anyone outside the purchase
    # request groups the field (and its button) simply does not exist - without this
    # every partner form raised an access error for them (client, 2026-10-20).
    quotation_count = fields.Integer(
        'Vendor Quotations', compute='_compute_quotation_count',
        groups='ebshel_purchase_request.group_pr_user')

    def _compute_quotation_count(self):
        # One grouped query for the whole recordset instead of a search_count per
        # partner (was N+1 when the field appears in list views).
        groups = self.env['ebshel.purchase.vendor.quotation']._read_group(
            [('vendor_id', 'in', self.ids)], ['vendor_id'], ['__count'])
        counts = {vendor.id: count for vendor, count in groups}
        for partner in self:
            partner.quotation_count = counts.get(partner.id, 0)

    def action_view_quotations(self):
        """Smart-button: every RFQ quotation this vendor has submitted."""
        self.ensure_one()
        return {
            'name': _('Vendor Quotations'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.vendor.quotation',
            'view_mode': 'list,form',
            'domain': [('vendor_id', '=', self.id)],
        }

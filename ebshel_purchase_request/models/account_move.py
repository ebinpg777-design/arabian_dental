# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        # When a vendor bill backed by PR-driven PO lines posts, the realized
        # spend now shows up as standard "achieved"; release the matching open
        # order encumbrance so it is not counted twice (achieved + committed).
        po_lines = posted.line_ids.mapped('purchase_line_id').filtered('request_line_ids')
        if po_lines:
            self.env['purchase.analytic.line']._sync_order_lines(po_lines)
        return posted

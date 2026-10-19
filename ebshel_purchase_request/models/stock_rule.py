# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from collections import defaultdict
from odoo import api, fields, models, _

class StockRule(models.Model):
    _inherit = 'stock.rule'

    @api.model
    def _run_buy(self, procurements):
        """Peel off procurements for products flagged 'purchase_request' and
        route them into internal Purchase Requests; everything else falls
        through to the standard RFQ generation."""
        to_request = []
        to_buy = []
        for procurement, rule in procurements:
            product = procurement.product_id
            if product.product_tmpl_id.with_company(
                    procurement.company_id).purchase_request:
                to_request.append((procurement, rule))
            else:
                to_buy.append((procurement, rule))

        if to_request:
            self._create_purchase_request(to_request)

        if to_buy:
            return super()._run_buy(to_buy)
        return True

    @api.model
    def _create_purchase_request(self, procurements):
        """Group procurements by (company, warehouse) into draft requests."""
        Request = self.env['ebshel.purchase.request'].sudo()
        RequestLine = self.env['ebshel.purchase.request.line'].sudo()

        grouped = defaultdict(list)
        for procurement, rule in procurements:
            company = rule.company_id or procurement.company_id
            warehouse = rule.warehouse_id
            grouped[(company.id, warehouse.id)].append((procurement, rule))

        for (company_id, _wh), proc_rules in grouped.items():
            # Reuse an existing draft auto-generated request for this origin/company.
            origins = {p.origin for p, _r in proc_rules if p.origin}
            origin = ", ".join(sorted(origins)) if origins else _('Procurement')
            request = Request.search([
                ('company_id', '=', company_id),
                ('state', '=', 'draft'),
                ('origin', '=', origin),
            ], limit=1)
            if not request:
                request = Request.create({
                    'company_id': company_id,
                    'origin': origin,
                    'source': 'auto_reorder',
                    'description': _('Auto-generated from procurement'),
                })
            for procurement, rule in proc_rules:
                line = RequestLine.create(
                    self._prepare_pr_line_vals(request, procurement, rule))
                moves = procurement.values.get('move_dest_ids')
                if moves:
                    moves.write({'created_purchase_request_line_id': line.id})
        return True

    @api.model
    def _prepare_pr_line_vals(self, request, procurement, rule):
        product = procurement.product_id
        return {
            'request_id': request.id,
            'product_id': product.id,
            'name': product._get_purchase_description(),
            'qty': procurement.product_qty,
            'uom_id': procurement.uom_id.id,
            'date_required': procurement.values.get('date_planned')
                             and fields.Date.to_date(procurement.values['date_planned']),
        }

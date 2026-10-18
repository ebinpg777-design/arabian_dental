# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

class StockMove(models.Model):
    _inherit = 'stock.move'

    allocation_ids = fields.One2many(
        'ebshel.purchase.request.allocation', 'stock_move_id',
        string='Request Allocations', copy=False,
    )
    created_purchase_request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='Created Request Line',
        index='btree_not_null', copy=False,
        help='Request line this procurement move generated (auto-PR reordering).',
    )

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._allocate_received()
        return moves

    def _allocate_received(self):
        """Distribute the received quantity of each done move across its
        request allocations, capped by each allocation's open quantity."""
        Allocation = self.env['ebshel.purchase.request.allocation']
        # Only inbound (receipt) moves carry received quantity to allocate.
        done_moves = self.filtered(
            lambda m: m.state == 'done'
            and m.location_dest_id.usage in ('internal', 'transit'))
        # Allocations are created against the PO line (not the stock move), so
        # resolve the fallback set for every relevant move in one query instead
        # of one search per move (was N+1 on bulk receipt validation).
        fallback_pols = done_moves.filtered(
            lambda m: not m.allocation_ids and m.purchase_line_id).purchase_line_id
        allocs_by_pol = {}
        if fallback_pols:
            for alloc in Allocation.search(
                    [('purchase_line_id', 'in', fallback_pols.ids)]):
                allocs_by_pol.setdefault(alloc.purchase_line_id.id, Allocation)
                allocs_by_pol[alloc.purchase_line_id.id] |= alloc
        for move in done_moves:
            allocs = move.allocation_ids
            if not allocs and move.purchase_line_id:
                allocs = allocs_by_pol.get(move.purchase_line_id.id, Allocation)
                # Bind them to the move so future receipts/backorders are scoped.
                if allocs:
                    allocs.filtered(lambda a: not a.stock_move_id).write(
                        {'stock_move_id': move.id})
            if not allocs:
                continue
            move_uom = move.product_uom
            remaining = move.quantity
            for alloc in allocs:
                if remaining <= 0:
                    break
                line_uom = alloc.product_uom_id or move_uom
                avail = move_uom._compute_quantity(remaining, line_uom) \
                    if (line_uom and move_uom) else remaining
                take = min(alloc.open_product_qty, avail)
                if take <= 0:
                    continue
                alloc.allocated_product_qty += take
                alloc._notify_allocation(take)
                consumed = line_uom._compute_quantity(take, move_uom) \
                    if (line_uom and move_uom) else take
                remaining -= consumed

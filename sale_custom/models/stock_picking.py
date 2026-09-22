# -*- coding: utf-8 -*-
from odoo import api, fields, models


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    # Delivery orders default ON: the lab hands over exactly what was ordered, and the
    # person clicking Validate at the counter must not have to type quantities, answer
    # "Create Backorder?" or read "Transfer trouble alert". Other operation types keep
    # stock's normal behaviour unless someone switches it on for them.
    lab_done_from_demand = fields.Boolean(
        string='Validate fills Done from Demand',
        compute='_compute_lab_done_from_demand', store=True, readonly=False,
        help="When Validate is clicked, every line's done quantity is set to its demand "
             "and the transfer is completed in one step — no backorder question, no "
             "'no quantities' error.")

    @api.depends('code')
    def _compute_lab_done_from_demand(self):
        for ptype in self:
            ptype.lab_done_from_demand = ptype.code == 'outgoing'


class StockPicking(models.Model):
    _inherit = "stock.picking"

    courier_company = fields.Char('Courier Company', copy=False)
    courier_option = fields.Char('Courier Option', copy=False)
    consignment_number = fields.Char('Consignment Number', copy=False)

    def _lab_fill_done_from_demand(self):
        """Done = demand on every open line, marked picked, so `button_validate` runs
        straight through `_action_done` (no backorder wizard, no zero-quantity error)."""
        for picking in self.filtered(
                lambda p: p.state not in ('done', 'cancel')
                and p.picking_type_id.lab_done_from_demand):
            if picking.state == 'draft':
                picking.action_confirm()
            for move in picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                if move.product_uom.compare(move.quantity, move.product_uom_qty) != 0:
                    move.quantity = move.product_uom_qty
                if not move.picked:
                    move.picked = True

    def button_validate(self):
        self._lab_fill_done_from_demand()
        if any(p.picking_type_id.lab_done_from_demand for p in self):
            # One click means one click: also skip stock_sms's "Send SMS to the
            # customer?" question (the lab talks to clinics over WhatsApp, not IAP SMS).
            self = self.with_context(skip_sms=True)
        return super().button_validate()

# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.tools import SQL

# A write of any of these on a done move changes a daily row (DESIGN.md §1.4 / §2.2).
TRIGGER_FIELDS = frozenset({
    'state', 'date', 'value', 'quantity', 'uom_id', 'location_id', 'location_dest_id',
    'company_id', 'product_id', 'restrict_partner_id', 'is_scrap', 'is_inventory', 'picked',
})


class StockMove(models.Model):
    _inherit = 'stock.move'

    asr_reason_id = fields.Many2one(
        'stock.scrap.reason.tag', string='Reason', index='btree_not_null', copy=False,
        help="Why this scrap, return or count adjustment happened. Used by the scrap and "
             "returns report and by the GST stock register.")
    asr_qty_before = fields.Float(
        'Quantity Before Count', digits='Product Unit of Measure', readonly=True, copy=False,
        help="System quantity of the quant when the count was applied.")
    asr_qty_counted = fields.Float(
        'Counted Quantity', digits='Product Unit of Measure', readonly=True, copy=False)
    asr_bom_planned_qty_unit = fields.Float(
        'Planned Quantity per Unit', digits=(16, 6), readonly=True,
        help="Component quantity the bill of materials asked for, per unit of finished product, "
             "frozen when the manufacturing order was confirmed. Backorder moves inherit it.")
    asr_planned_estimated = fields.Boolean(
        'Planned Quantity Estimated', readonly=True,
        help="The planned quantity was backfilled from the move demand when the module was "
             "installed, not frozen at confirmation.")

    # ------------------------------------------------------------------
    # Reasons: Odoo 20 scraps and returns without a stock.scrap record or a return wizard
    # ------------------------------------------------------------------
    def _prepare_scrap_move_vals(self):
        # A scrap is the move itself in Odoo 20 (no stock.scrap): its first reason tag
        # becomes the reason, as the scrap's tag did when stock.scrap made the move.
        vals = super()._prepare_scrap_move_vals()
        if self.scrap_reason_tag_ids and not self.asr_reason_id:
            vals['asr_reason_id'] = self.scrap_reason_tag_ids[0].id
        return vals

    # ------------------------------------------------------------------
    # Dirty queue hooks
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        # a return line added to a return that already has its reason carries it too
        for move in moves:
            if move.origin_returned_move_id and not move.asr_reason_id and move.picking_id.asr_reason_id:
                move.asr_reason_id = move.picking_id.asr_reason_id
        done = moves.filtered(lambda m: m.state == 'done')
        if done:
            self.env['asr.stock.dirty']._mark_moves(done)
        return moves

    def write(self, vals):
        touched = TRIGGER_FIELDS.intersection(vals)
        if not touched or (touched == {'state'} and vals.get('state') != 'done'):
            return super().write(vals)
        becomes_done = vals.get('state') == 'done'
        before = {}
        for move in self:
            if move.state == 'done' or becomes_done:
                before[move.id] = move.date
        res = super().write(vals)
        if before:
            moves = self.browse(list(before)).filtered(lambda m: m.state == 'done')
            self.env['asr.stock.dirty']._mark_moves(moves, dates_by_move=before)
        return res

    # ------------------------------------------------------------------
    # Planned consumption per unit (report #2)
    # ------------------------------------------------------------------
    def _asr_set_planned_qty_unit(self):
        """Freeze the BoM quantity per unit of finished product on raw moves."""
        for move in self:
            production = move.raw_material_production_id
            if not production or not move.bom_line_id or move.asr_bom_planned_qty_unit:
                continue
            bom = move.bom_line_id.bom_id
            bom_qty_in_product_uom = bom.uom_id._compute_quantity(
                bom.product_qty, production.product_id.uom_id, round=False) or 1.0
            line_qty = move.bom_line_id.uom_id._compute_quantity(
                move.bom_line_id.product_qty, move.product_id.uom_id, round=False)
            move.asr_bom_planned_qty_unit = line_qty / bom_qty_in_product_uom
            move.asr_planned_estimated = False

    @api.model
    def _asr_backfill_planned_qty(self):
        """Estimate the planned quantity on raw moves of orders confirmed before the install.

        One SQL statement: demand in product unit / order quantity in product unit,
        flagged estimated. Orders confirmed afterwards get the exact BoM figure.
        """
        self.flush_model()
        self.env.cr.execute(SQL("""
            UPDATE stock_move sm
               SET asr_bom_planned_qty_unit = (sm.product_uom_qty * um.factor / up.factor)
                                              / (mo.product_qty * umo.factor / upf.factor),
                   asr_planned_estimated = TRUE
              FROM mrp_production mo
              JOIN product_product ppf ON ppf.id = mo.product_id
              JOIN product_template ptf ON ptf.id = ppf.product_tmpl_id
              JOIN uom_uom upf ON upf.id = ptf.uom_id
              JOIN uom_uom umo ON umo.id = mo.uom_id,
                   uom_uom um, product_product pp, product_template pt, uom_uom up
             WHERE sm.raw_material_production_id = mo.id
               AND um.id = sm.uom_id
               AND pp.id = sm.product_id AND pt.id = pp.product_tmpl_id AND up.id = pt.uom_id
               AND COALESCE(sm.asr_bom_planned_qty_unit, 0) = 0
               AND sm.state != 'cancel'
               AND mo.product_qty <> 0 AND umo.factor <> 0 AND upf.factor <> 0 AND up.factor <> 0
        """))
        self.invalidate_model(['asr_bom_planned_qty_unit', 'asr_planned_estimated'])


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # Odoo 20 makes a return straight from the picking (no stock.return.picking wizard):
    # the reason is chosen on the return itself and stored on each of its moves.
    asr_reason_id = fields.Many2one(
        'stock.scrap.reason.tag', string='Return Reason Tag', copy=False,
        domain="[('asr_usage', 'in', ('return', 'any'))]",
        help="Stored on every return move; used by the scrap and returns report.")

    def write(self, vals):
        res = super().write(vals)
        if 'asr_reason_id' in vals:
            for picking in self:
                picking.move_ids.filtered('origin_returned_move_id').write(
                    {'asr_reason_id': picking.asr_reason_id.id})
        return res


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    _ASR_TRIGGER_FIELDS = frozenset({
        'quantity', 'quantity_product_uom', 'location_id', 'location_dest_id', 'owner_id', 'lot_id',
        'date', 'picked', 'move_id',
    })

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        done_moves = lines.move_id.filtered(lambda m: m.state == 'done')
        if done_moves:
            self.env['asr.stock.dirty']._mark_moves(done_moves)
        return lines

    def write(self, vals):
        if not self._ASR_TRIGGER_FIELDS.intersection(vals):
            return super().write(vals)
        before = self.move_id.filtered(lambda m: m.state == 'done')
        res = super().write(vals)
        after = self.move_id.filtered(lambda m: m.state == 'done')
        moves = before | after
        if moves:
            self.env['asr.stock.dirty']._mark_moves(moves)
        return res

    def unlink(self):
        moves = self.move_id.filtered(lambda m: m.state == 'done')
        res = super().unlink()
        if moves:
            self.env['asr.stock.dirty']._mark_moves(moves)
        return res

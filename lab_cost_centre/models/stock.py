# -*- coding: utf-8 -*-
"""Material on the move, and whose material it is.

The lab's stores are already one per department. That makes every movement
answerable: what goes into the Ceramic store is Ceramic's, and what leaves it
for a consumption account is what Ceramic used.
"""
from odoo import api, models


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'lab.cost.centre.mixin']

    def _derive_department(self):
        """The department at the receiving end, or at the giving end.

        A transfer into a department store is that department's; one out of it
        into a consumption account is also that department's - the material is
        being used by them. Only a move between two other places has nobody, and
        that is the main store's business.
        """
        self.ensure_one()
        if self.location_dest_id.department_id:
            return self.location_dest_id.department_id
        return self.location_id.department_id

    @api.depends('location_id', 'location_dest_id')
    def _compute_department_id(self):
        return super()._compute_department_id()


class StockMove(models.Model):
    _name = 'stock.move'
    _inherit = ['stock.move', 'lab.cost.centre.mixin']

    def _derive_department(self):
        self.ensure_one()
        if self.location_dest_id.department_id:
            return self.location_dest_id.department_id
        if self.location_id.department_id:
            return self.location_id.department_id
        if self.raw_material_production_id.department_id:
            return self.raw_material_production_id.department_id
        if self.production_id.department_id:
            return self.production_id.department_id
        return self.picking_id.department_id

    @api.depends('location_id', 'location_dest_id', 'picking_id.department_id',
                 'raw_material_production_id.department_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    def _account_entry_move(self, qty, description, svl_id, cost):
        """Carry the department into the valuation entry.

        A stock move's accounting entry is created by core with no idea of who
        moved the goods; posting it into the department's cost centre is what
        turns "consumption" into "Ceramic consumed 4,800 this month".
        """
        moves = super()._account_entry_move(qty, description, svl_id, cost)
        centre = self.cost_centre_id
        if centre and moves:
            lines = moves.line_ids._lab_analytic_candidates() \
                if hasattr(moves.line_ids, '_lab_analytic_candidates') else moves.line_ids
            for line in lines:
                if not line.analytic_distribution:
                    line.analytic_distribution = {str(centre.id): 100.0}
        return moves

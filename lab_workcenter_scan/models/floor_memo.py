# -*- coding: utf-8 -*-
"""What changes the floor drops the boards' shared view of it (`report.lab.floor`
`_floor_shared`): a step taken, handed on, moved or undone, an order that changes
state, a delivery done or returned. Everything else - a timer, a note, a name on a
job - leaves the queues where they were and keeps the shared view."""
from odoo import api, models

WORKORDER_FIELDS = frozenset(('state', 'accepted_at', 'handed_over_at', 'workcenter_id',
                              'production_id', 'arch', 'sequence', 'company_id'))
PRODUCTION_FIELDS = frozenset(('state', 'sale_id', 'company_id'))
PICKING_FIELDS = frozenset(('state', 'sale_id', 'picking_type_id', 'company_id'))
ORDER_FIELDS = frozenset(('active',))


class FloorMemoWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['report.lab.floor']._floor_forget()
        return records

    def write(self, vals):
        res = super().write(vals)
        if WORKORDER_FIELDS.intersection(vals):
            self.env['report.lab.floor']._floor_forget()
        return res

    def unlink(self):
        res = super().unlink()
        self.env['report.lab.floor']._floor_forget()
        return res


class FloorMemoProduction(models.Model):
    _inherit = 'mrp.production'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['report.lab.floor']._floor_forget()
        return records

    def write(self, vals):
        res = super().write(vals)
        if PRODUCTION_FIELDS.intersection(vals):
            self.env['report.lab.floor']._floor_forget()
        return res


class FloorMemoPicking(models.Model):
    _inherit = 'stock.picking'

    def write(self, vals):
        res = super().write(vals)
        if PICKING_FIELDS.intersection(vals):
            self.env['report.lab.floor']._floor_forget()
        return res


class FloorMemoSaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        res = super().write(vals)
        if ORDER_FIELDS.intersection(vals):
            self.env['report.lab.floor']._floor_forget()
        return res

# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    scan_id = fields.Many2one(
        'lab.scan', string='Scan Register', readonly=True, copy=False, index='btree_not_null',
        help="The scan register this order was made from.")
    scan_line_id = fields.Many2one(
        'lab.scan.line', string='Scan Patient', readonly=True, copy=False, index='btree_not_null')
    # Which plate printed this patient's models, and on which machine: a model that
    # breaks in finishing or a resin batch that turns out bad is traced from the order
    # itself, without opening the register. Written by the register's Create Orders -
    # the plate of the patient's latest printed line - never by hand.
    print_order_id = fields.Many2one(
        'lab.scan.print.order', string='Print Order', readonly=True, copy=False, index='btree_not_null',
        help="The plate this patient's models came off, set when the order was made from the register.")
    print_printer_id = fields.Many2one(
        related='print_order_id.printer_id', string='Printed On',
        help="The machine the plate ran on.")
    print_resin_batch = fields.Char(
        related='print_order_id.resin_batch', string='Resin Batch',
        help="The bottle or lot that was in the vat.")

    def action_view_scan(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan',
                'res_id': self.scan_id.id, 'view_mode': 'form'}

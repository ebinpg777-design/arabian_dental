# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    delay_reason = fields.Char('Reason Of Delay')
    remarks = fields.Text('Remarks')


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # Append the source Sale Order to the MO display name, and let it be searched.
    _rec_names_search = ['name', 'sale_id']

    @api.depends('name', 'sale_id')
    def _compute_display_name(self):
        for mrp in self:
            name = mrp.name or ''
            if mrp.sale_id:
                name = '%s (%s)' % (name, mrp.sale_id.name)
            mrp.display_name = name


class StockLot(models.Model):
    # stock.production.lot was renamed stock.lot in Odoo 16+
    _inherit = 'stock.lot'

    # Column order kept identical to the Odoo 10 module so the migrated
    # custom_mrp_lot_rel table is read correctly (column1 holds the lot id).
    mrp_ids = fields.Many2many(
        'mrp.production', 'custom_mrp_lot_rel', 'mrp_id', 'lot_id', 'MOs')
    product_qty = fields.Float('Quantity')
    po_ref = fields.Char('PO Ref')
    date = fields.Date('Date')

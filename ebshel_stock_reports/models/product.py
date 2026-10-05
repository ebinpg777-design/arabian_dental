# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

GST_STOCK_CLASSES = [
    ('raw', 'Raw Material / Input'),
    ('wip', 'Work in Progress'),
    ('finished', 'Finished Goods'),
    ('consumable', 'Consumable / Store'),
    ('capital', 'Capital Goods'),
    ('trading', 'Trading Goods'),
    ('scrap', 'Scrap / Waste'),
    ('other', 'Other'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        res = super().write(vals)
        if 'categ_id' in vals:
            # The cost method may have changed with the category: rebuild those products.
            self.env['asr.stock.dirty']._mark_products(self.product_variant_ids)
        return res


class ProductCategory(models.Model):
    _inherit = 'product.category'

    asr_gst_stock_class = fields.Selection(
        GST_STOCK_CLASSES, string='GST Stock Class',
        help="Grouping of the GST stock register (Rule 56 of the CGST Rules): raw materials, "
             "finished goods, scrap and so on. Products inherit it from their category.")

    def write(self, vals):
        res = super().write(vals)
        if 'property_cost_method' in vals:
            products = self.env['product.product'].with_context(active_test=False).search(
                [('categ_id', 'in', self.ids)])
            self.env['asr.stock.dirty']._mark_products(products, company=self.env.company)
        return res

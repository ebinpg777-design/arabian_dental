# -*- coding: utf-8 -*-
"""What kind of work an order is, for the list's side panel.

(client, 2026-11-26: "In the SO search panel department filter is not needed,
please add the Product Category filter")
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderProductCategories(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Category = cls.env['product.category']
        cls.crowns, cls.dentures = Category.create({'name': 'ZZ CROWNS'}), Category.create({'name': 'ZZ DENTURES'})
        make = lambda name, categ: cls.env['product.product'].create(   # noqa: E731
            {'name': name, 'type': 'consu', 'categ_id': categ.id})
        cls.crown, cls.denture = make('ZZ Crown', cls.crowns), make('ZZ Denture', cls.dentures)
        cls.clinic = cls.env['res.partner'].create({'name': 'ZZ Category Clinic'})

    def _order(self, *products):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 1}) for p in products]})

    def test_an_order_is_under_every_category_it_carries(self):
        both, one = self._order(self.crown, self.denture), self._order(self.crown)
        self.assertEqual(both.product_categ_ids, self.crowns | self.dentures)
        self.assertEqual(one.product_categ_ids, self.crowns)
        found = self.env['sale.order'].search([('product_categ_ids', 'in', self.dentures.ids)])
        self.assertEqual(found, both)

    def test_it_follows_the_lines_and_the_products_category(self):
        order = self._order(self.crown)
        order.order_line.product_id = self.denture
        self.assertEqual(order.product_categ_ids, self.dentures)
        self.denture.categ_id = self.crowns
        self.assertEqual(order.product_categ_ids, self.crowns, "a product filed again takes its orders along")

    def test_the_panel_lists_the_categories_with_their_orders(self):
        self._order(self.crown, self.denture)
        self._order(self.crown)
        self.env.flush_all()
        panel = self.env['sale.order'].search_panel_select_multi_range(
            'product_categ_ids', enable_counters=True,
            search_domain=[('partner_id', '=', self.clinic.id)])
        counts = {value['display_name']: value['__count'] for value in panel['values']}
        self.assertEqual((counts.get('ZZ CROWNS'), counts.get('ZZ DENTURES')), (2, 1))

    def test_the_panel_has_the_category_and_not_the_department(self):
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale.view_sales_order_filter').id, 'search')['arch']
        panel = arch[arch.index('<searchpanel'):arch.index('</searchpanel>')]
        self.assertIn('product_categ_ids', panel)
        self.assertNotIn('department_id', panel)
        self.assertLess(panel.index('product_categ_ids'), panel.index('team_id'), "above the routes")

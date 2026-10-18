# -*- coding: utf-8 -*-
"""A plain salesperson opens a sale order that has a dispatch.

The order form carries the Dispatch stat button, and its count was read as the
user: office sales staff hold no right on lab.delivery (Track a Work answers them),
so the count raised and the whole order form came down with an Access Error - no
plain salesperson could open a sale order. Read the way the web client reads it:
every field of the form view, as that user, with the cache dropped first (a value
root already read would otherwise be served without any access check).
"""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalesUserOpensOrder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seller = cls.env['res.users'].create({
            'name': 'Office Seller', 'login': 'deliv_office_seller',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.clinic = cls.env['res.partner'].create({'name': 'Office Seller Clinic'})
        product = cls.env['product.product'].create({
            'name': 'Office Seller Appliance', 'type': 'consu', 'list_price': 500.0})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'user_id': cls.seller.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.env['lab.delivery'].create({
            'sale_order_id': cls.order.id, 'partner_id': cls.clinic.id,
            'scheduled_date': fields.Datetime.now()})

    def test_the_seller_holds_no_right_on_dispatches(self):
        """If this ever stops being true the test below proves nothing."""
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env['lab.delivery'].with_user(self.seller).search([('sale_order_id', '=', self.order.id)])

    def test_a_salesperson_opens_an_order_with_a_dispatch(self):
        Order = self.env['sale.order'].with_user(self.seller)
        views = Order.get_views([(False, 'form')])
        form_fields = views['models']['sale.order']['fields']
        self.assertIn('lab_delivery_count', form_fields, "fixture: the Dispatch button is on the form")
        spec = {name: ({'fields': {'display_name': {}}} if desc['type'] == 'many2one' else {})
                for name, desc in form_fields.items()}
        self.env.invalidate_all()
        data = self.order.with_user(self.seller).web_read(spec)[0]
        self.assertEqual(data['lab_delivery_count'], 1, "the count is shown, not an error")
        self.assertEqual(data['state'], 'sale')

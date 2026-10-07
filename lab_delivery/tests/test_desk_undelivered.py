# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDesksWithDeliveries(TransactionCase):
    """lab_fieldwork's three desks carry an "Undelivered dispatches" panel and a
    see-all list whose rows only exist once this module is installed
    (`lab.desk._see_all_specs()['undelivered']` reads `lab.delivery._live_ids()`).
    Both must answer, as the managers who read them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.ops = cls.env['res.users'].create({
            'name': 'Desk Ops', 'login': 'desk_ops_dlv', 'tz': 'Asia/Kolkata',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('lab_fieldwork.group_fieldwork_ops_manager').id])]})
        cls.mkt = cls.env['res.users'].create({
            'name': 'Desk Marketing', 'login': 'desk_mkt_dlv', 'tz': 'Asia/Kolkata',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('lab_fieldwork.group_fieldwork_marketing_manager').id])]})
        cls.carrier = cls.env['res.users'].create({
            'name': 'Desk Carrier', 'login': 'desk_carrier_dlv',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('lab_fieldwork.group_fieldwork_executive').id])]})
        clinic = cls.env['res.partner'].create({'name': 'Desk Clinic', 'is_company': True})
        product = cls.env['product.product'].create({
            'name': 'Desk Appliance', 'type': 'consu', 'list_price': 10.0})
        order = cls.env['sale.order'].create({
            'partner_id': clinic.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.delivery = cls.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': cls.carrier.id,
            'scheduled_date': fields.Datetime.now()})

    def test_both_desks_show_the_undelivered_panel(self):
        self.env.flush_all()
        for manager, method in ((self.ops, 'get_ops_desk'), (self.mkt, 'get_marketing_desk')):
            data = getattr(self.env['lab.desk'].with_user(manager), method)()
            panel = data['undelivered']
            self.assertGreaterEqual(panel['total'], 1, method)
            self.assertIn(self.delivery.id, [r['id'] for r in panel['rows']], method)

    def test_the_see_all_list_opens_the_live_dispatches(self):
        self.env.flush_all()
        for manager in (self.ops, self.mkt):
            action = self.env['lab.desk'].with_user(manager).action_see_all('undelivered')
            self.assertEqual(action['res_model'], 'lab.delivery')
            listed = self.env['lab.delivery'].with_user(manager).search(action['domain'])
            self.assertIn(self.delivery, listed)

# -*- coding: utf-8 -*-
"""The lab's orders carry no "Appliance Type". (client, 2026-09-30)

Fixed / removable / clear retainer is an orthodontics question the suite was forked
with; a dental lab names a case by its works. It was required on a draft order, so
every order stopped for a question with no answer here. It must not come back with
a port from the ortho side, on the model or on any screen.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNoApplianceType(TransactionCase):

    def test_the_order_has_no_such_field(self):
        self.assertNotIn('appliance_type', self.env['sale.order']._fields)

    def test_no_order_screen_asks_for_it(self):
        Order = self.env['sale.order']
        for view_type in ('form', 'list', 'search'):
            arch = Order.get_view(view_type=view_type)['arch']
            self.assertNotIn('appliance_type', arch, view_type)
        for xmlid in ('sale_custom.view_order_due_list', 'sale_custom.view_order_due_search'):
            view = self.env.ref(xmlid, raise_if_not_found=False)
            if view:
                arch = Order.get_view(view.id, view.type)['arch']
                self.assertNotIn('appliance_type', arch, xmlid)

    def test_the_order_list_print_has_no_appliance_filter(self):
        Wizard = self.env['sale.order.list.report']
        self.assertFalse([f for f in Wizard._fields if f.startswith('type_')])
        self.assertNotIn('appliance', dict(Wizard._fields['group_by'].selection))

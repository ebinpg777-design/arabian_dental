# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseCostAccess(TransactionCase):
    """Every bill of materials with its cost, price and margin, read as superuser: for
    the people the Case Cost menus are for, not anybody logged in. (2026-10-22)"""

    def test_case_cost_is_for_the_people_its_menus_are_for(self):
        Users = self.env['res.users'].with_context(no_reset_password=True)
        base = self.env.ref('base.group_user')
        outsider = Users.create({'name': 'Cost Outsider', 'login': 'cost.outsider@test',
                                 'group_ids': [(6, 0, [base.id])]})
        manager = Users.create({'name': 'Cost MRP Manager', 'login': 'cost.mrp.manager@test',
                                'group_ids': [(6, 0, [base.id, self.env.ref('mrp.group_mrp_manager').id])]})
        Cost = self.env['lab.case.cost']
        with self.assertRaises(AccessError):
            Cost.with_user(outsider).get_routes()
        self.assertIsNotNone(Cost.with_user(manager).get_routes())

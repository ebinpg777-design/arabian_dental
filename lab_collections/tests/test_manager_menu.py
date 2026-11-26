# -*- coding: utf-8 -*-
"""Collections on the Field Work bar for field work managers.
(client, 2026-11-26: "add the menu of Collections in field work menu for field work managers")"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCollectionsManagerMenu(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user').id

        def user(login, *xmlids):
            return cls.env['res.users'].create({
                'name': login, 'login': login,
                'group_ids': [(6, 0, [base] + [cls.env.ref(x).id for x in xmlids])]})

        cls.manager = user('menu.fw.manager@test', 'lab_fieldwork.group_fieldwork_manager')
        cls.executive = user('menu.fw.executive@test', 'lab_fieldwork.group_fieldwork_executive',
                             'sales_team.group_sale_salesman')
        cls.manager_menu = cls.env.ref('lab_collections.menu_collections_fieldwork_manager')
        cls.executive_menu = cls.env.ref('lab_collections.menu_collections_fieldwork')

    def _menus(self, user):
        return self.env['ir.ui.menu'].with_user(user).load_menus(False)

    def test_a_field_work_manager_has_collections_under_field_work(self):
        menus = self._menus(self.manager)
        self.assertIn(self.manager_menu.id, menus)
        self.assertEqual(menus[self.manager_menu.id]['name'], 'Collections')
        root = self.env.ref('lab_fieldwork.menu_fieldwork_root').id
        self.assertIn(self.manager_menu.id, menus[root]['children'])
        # it opens the whole company for them, not a round
        self.assertIsNone(self.env['lab.collection.performance'].with_user(self.manager)._viewer_route_ids())

    def test_an_executive_keeps_my_collections_only(self):
        menus = self._menus(self.executive)
        self.assertIn(self.executive_menu.id, menus)
        self.assertNotIn(self.manager_menu.id, menus)

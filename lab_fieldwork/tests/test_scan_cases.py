# -*- coding: utf-8 -*-
"""A case that came as a scan, and the home-screen notice while its order waits.

(client, 2026-11-27: "Executives can mark the cases as Scan then it will become 3D
Model Print sale orders. Show a notification in the main view to the assigned users
when these scanned orders are not confirmed")
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestScanCases(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Scan Exec', 'login': 'scan_exec', 'group_ids': [(6, 0, groups)]})
        route = cls.env['crm.team'].create({'name': 'Scan Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Scan Clinic', 'is_clinic': True, 'team_id': route.id})
        cls.model = cls.env['product.product'].create(
            {'name': 'ZZ Printed Model', 'sale_ok': True, 'list_price': 300})
        office_groups = [G('base.group_user').id, G('sales_team.group_sale_salesman_all_leads').id]
        cls.told = cls.env['res.users'].create({
            'name': 'Scan Told', 'login': 'scan_told', 'group_ids': [(6, 0, office_groups)]})
        cls.other = cls.env['res.users'].create({
            'name': 'Scan Other', 'login': 'scan_other', 'group_ids': [(6, 0, office_groups)]})
        cls.env.company.lab_scan_notify_user_ids = [(6, 0, cls.told.ids)]
        # earlier 3D print orders of this database are not this test's business
        cls.env['sale.order'].search(
            [('is_3d_model_print', '=', True), ('state', 'in', ('draft', 'sent'))]
        ).write({'is_3d_model_print': False})

    def _scan_case(self):
        case = self.env['lab.case'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'patient': 'Scan Patient', 'is_scan': True,
            'line_ids': [(0, 0, {'product_id': self.model.id, 'ul': 'upper'})]})
        case.action_submit()
        return case.sudo().sale_order_id

    def _notice(self, user):
        notices = self.env['res.users'].with_user(user).ebshel_home_notices()
        return next((n for n in notices if n['key'] == 'lab_scan_orders'), None)

    def test_a_scan_case_makes_a_3d_model_print_order(self):
        order = self._scan_case()
        self.assertTrue(order.is_3d_model_print)
        self.assertEqual(order.scanned_impression, 'Yes')

    def test_the_assigned_people_are_told_until_it_is_confirmed(self):
        self.assertIsNone(self._notice(self.told), "nothing waiting, nothing said")
        order = self._scan_case()
        notice = self._notice(self.told)
        self.assertTrue(notice)
        self.assertEqual(notice['count'], 1)
        self.assertEqual(notice['action']['res_model'], 'sale.order')
        self.assertIn(order, self.env['sale.order'].search(notice['action']['domain']))
        self.assertIsNone(self._notice(self.other), "only the people named in settings")
        order.sudo().write({'state': 'sale'})
        self.assertIsNone(self._notice(self.told), "confirmed: the notice goes")

    def test_a_case_not_marked_scan_is_an_ordinary_order(self):
        case = self.env['lab.case'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'patient': 'Plain Patient',
            'line_ids': [(0, 0, {'product_id': self.model.id, 'ul': 'upper'})]})
        case.action_submit()
        self.assertFalse(case.sudo().sale_order_id.is_3d_model_print)

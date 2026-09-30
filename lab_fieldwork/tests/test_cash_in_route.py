# -*- coding: utf-8 -*-
"""Cash in offers the executive the clinics on their own route. (client, 2026-09-30)

The visit form always did; the Cash in form had no domain at all and listed every
contact in the database.
"""
from ast import literal_eval

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCashInRoute(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        mine = cls.env['crm.team'].create({'name': 'Cash Mine'})
        other = cls.env['crm.team'].create({'name': 'Cash Other'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Cash Exec', 'login': 'cash_route_exec',
            'group_ids': [(6, 0, [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.manager = cls.env['res.users'].create({
            'name': 'Cash Manager', 'login': 'cash_route_manager',
            'group_ids': [(6, 0, [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_manager').id])]})
        mine.user_id = cls.exec_user.id
        cls.here = cls.env['res.partner'].create({'name': 'CASH HERE', 'is_clinic': True, 'team_id': mine.id})
        cls.doctor = cls.env['res.partner'].create({'name': 'DR AT HERE', 'parent_id': cls.here.id})
        cls.away = cls.env['res.partner'].create({'name': 'CASH AWAY', 'is_clinic': True, 'team_id': other.id})
        cls.loose = cls.env['res.partner'].create({'name': 'SOME SUPPLIER'})
        cls.ours = cls.here | cls.doctor | cls.away | cls.loose

    def _offered(self, user, model):
        domain = literal_eval(self.env[model]._fields['partner_id'].domain)
        return self.env['res.partner'].with_user(user).search(domain + [('id', 'in', self.ours.ids)])

    def test_cash_in_offers_only_my_route(self):
        for model in ('lab.collect.cash', 'lab.cash.collection'):
            with self.subTest(model=model):
                self.assertEqual(self._offered(self.exec_user, model), self.here | self.doctor,
                                 "the clinic and its doctor - not another route, not a supplier")

    def test_a_manager_is_offered_everyone(self):
        self.assertEqual(self._offered(self.manager, 'lab.collect.cash'), self.ours)

    def test_it_is_the_visit_rule(self):
        self.assertEqual(self.env['lab.collect.cash']._fields['partner_id'].domain,
                         self.env['lab.visit']._fields['partner_id'].domain)

# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestRouteMembership(TransactionCase):
    """One person on several Sales Routes (client, 2026-11-27)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.person = new_test_user(cls.env, login='route_member_t', groups='sales_team.group_sale_salesman')
        Team = cls.env['crm.team']
        cls.wayanad = Team.create({'name': 'T WAYANAD'})
        cls.mananthavadi = Team.create({'name': 'T MANANTHAVADI'})

    def _members(self, team):
        return team.crm_team_member_ids.filtered('active').user_id

    def test_second_route_keeps_the_first(self):
        self.env['res.config.settings'].create({'lab_route_membership_multi': True}).execute()
        self.wayanad.member_ids = [(4, self.person.id)]
        self.mananthavadi.member_ids = [(4, self.person.id)]
        self.assertIn(self.person, self._members(self.wayanad))
        self.assertIn(self.person, self._members(self.mananthavadi))
        self.assertFalse(self.mananthavadi.member_warning)

    def test_off_moves_them_as_before(self):
        self.env['res.config.settings'].create({'lab_route_membership_multi': False}).execute()
        self.wayanad.member_ids = [(4, self.person.id)]
        self.mananthavadi.member_ids = [(4, self.person.id)]
        self.assertNotIn(self.person, self._members(self.wayanad))
        self.assertIn(self.person, self._members(self.mananthavadi))

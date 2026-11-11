# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests import tagged
from odoo.addons.l10n_in.tests.common import L10nInTestInvoicingCommon


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestRoutePlaceOfSupply(L10nInTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tn = cls.env.ref('base.state_in_tn')
        cls.kl = cls.env.ref('base.state_in_kl')
        cls.route = cls.env['crm.team'].sudo().create({'name': 'Ooty Route', 'place_of_supply_id': cls.tn.id})
        cls.bare_route = cls.env['crm.team'].sudo().create({'name': 'Route With No State'})

    def _invoice(self, partner):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': '2023-05-20',
            'invoice_line_ids': [Command.create({'product_id': self.product_a.id, 'quantity': 1.0,
                                                 'price_unit': 100.0})]})
        return move

    def test_a_new_customer_on_the_team_gets_its_state(self):
        clinic = self.env['res.partner'].create({'name': 'Hill Dental', 'team_id': self.route.id})
        self.assertEqual((clinic.state_id, clinic.country_id.code), (self.tn, 'IN'))
        kept = self.env['res.partner'].create({'name': 'Kerala Dental', 'team_id': self.route.id,
                                               'state_id': self.kl.id, 'country_id': self.country_in.id})
        self.assertEqual(kept.state_id, self.kl, "a state somebody set stays")
        moved = self.env['res.partner'].create({'name': 'Moved Later'})
        self.assertFalse(moved.state_id)
        moved.team_id = self.route
        self.assertEqual(moved.state_id, self.tn, "moving onto the team fills the state")
        abroad = self.env['res.partner'].create({'name': 'Dubai Lab', 'country_id': self.env.ref('base.ae').id,
                                                 'team_id': self.route.id})
        self.assertFalse(abroad.state_id, "a foreign contact is not put in an Indian state")

    def test_an_invoice_takes_the_team_state_when_the_customer_has_none(self):
        clinic = self.env['res.partner'].create({'name': 'Old Clinic', 'team_id': self.bare_route.id})
        self.assertFalse(clinic.state_id)
        self.bare_route.place_of_supply_id = self.tn        # set after the customer existed
        self.assertFalse(clinic.state_id, "the team's state is not pushed back by itself")
        move = self._invoice(clinic)
        self.assertEqual(move.l10n_in_state_id, self.tn)
        self.assertNotEqual(move.l10n_in_state_id, self.default_company.state_id)
        self.assertIn('Inter', move.fiscal_position_id.name or '', "an inter-state sale gets the inter-state position")
        plain = self.env['res.partner'].create({'name': 'No Team, No State'})
        self.assertEqual(self._invoice(plain).l10n_in_state_id, self.default_company.state_id,
                         "without a team Odoo's own fallback stands")

    def test_apply_fills_the_missing_states_or_all_of_them(self):
        self.bare_route.place_of_supply_id = self.tn
        a = self.env['res.partner'].create({'name': 'A', 'team_id': self.bare_route.id, 'state_id': self.kl.id,
                                            'country_id': self.country_in.id})
        b = self.env['res.partner'].with_context(place_of_supply_filling=True).create({
            'name': 'B', 'team_id': self.bare_route.id})
        b.with_context(place_of_supply_filling=True).write({'state_id': False})
        self.assertEqual(self.bare_route.place_of_supply_missing_count, 1)
        wizard = self.env['team.place.of.supply.apply'].sudo().create({'team_id': self.bare_route.id})
        self.assertIn('1 customer', wizard.summary)
        wizard.action_apply()
        self.assertEqual((a.state_id, b.state_id, b.country_id.code), (self.kl, self.tn, 'IN'))
        wizard = self.env['team.place.of.supply.apply'].sudo().create({'team_id': self.bare_route.id, 'mode': 'all'})
        wizard.action_apply()
        self.assertEqual(a.state_id, self.tn, "'every customer' overwrites")

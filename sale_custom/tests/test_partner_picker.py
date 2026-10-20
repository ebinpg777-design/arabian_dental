# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPartnerPicker(TransactionCase):
    """The customer dropdown: name, then the city and the Sales Route, muted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.route = cls.env['crm.team'].create({'name': 'COONOOR'})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'DR. INIYAN', 'street': 'Bedford Circle', 'city': 'Wellington', 'team_id': cls.route.id})

    def _label(self, partner):
        return partner.with_context(formatted_display_name=True).display_name

    def test_the_dropdown_shows_city_then_route(self):
        self.assertEqual(self._label(self.clinic), 'DR. INIYAN\t--Wellington--\t--COONOOR--')
        # the field itself, once chosen, keeps the plain name
        self.assertEqual(self.clinic.display_name, 'DR. INIYAN')

    def test_a_city_cannot_break_the_markup_or_the_line(self):
        self.clinic.city = 'Near --bus-- stand **main** `A`\nlane, a very long place name indeed beyond forty'
        city = self._label(self.clinic).split('\t')[1]
        self.assertTrue(city.startswith('--') and city.endswith('--'))
        inner = city[2:-2]
        for bad in ('--', '**', '`', '\n'):
            self.assertNotIn(bad, inner)
        self.assertLessEqual(len(inner), 40)

    def test_a_city_named_like_the_route_is_said_once(self):
        self.clinic.city = 'coonoor '
        self.assertEqual(self._label(self.clinic), 'DR. INIYAN\t--COONOOR--')

    def test_without_a_city_only_the_route(self):
        self.clinic.city = False
        self.assertEqual(self._label(self.clinic), 'DR. INIYAN\t--COONOOR--')

    def test_a_contact_shows_its_clinics_city(self):
        # a contact shares its clinic's address (Odoo keeps the two in step)
        contact = self.env['res.partner'].create({'name': 'Reception', 'parent_id': self.clinic.id})
        label = self._label(contact)
        self.assertIn('--Reception--', label)
        self.assertIn('--Wellington--', label)

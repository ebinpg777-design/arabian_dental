# -*- coding: utf-8 -*-
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestShadeSearch(TransactionCase):
    """Typing "A2" finds A2 first, and an order line cannot invent a shade.
    (client, 2026-10-21)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Shade = cls.env['product.colour']
        # created the way the live list grew: combinations first, the plain shade later
        cls.combos = Shade.create([{'name': n} for n in (
            'A2.5', 'A3,A2,A1', '2M2,A2', 'A1,A2', 'A2,A3', 'A2 D2', 'A21', 'GIVEN,A2')])
        cls.a2 = Shade.create({'name': 'A2'})
        cls.a3 = Shade.create({'name': 'A3'})

    def _names(self, term, limit=8):
        found = self.env['product.colour'].name_search(term, limit=limit)
        return [name for _id, name in found]

    def test_the_exact_shade_comes_first(self):
        self.assertEqual(self._names('A2')[0], 'A2')
        self.assertEqual(self._names('a2 ')[0], 'A2', "case and stray spaces do not matter")

    def test_shades_starting_with_it_come_before_the_ones_containing_it(self):
        names = self._names('A2', limit=100)
        starts = [n for n in names if n.upper().startswith('A2')]
        self.assertEqual(names[:len(starts)], starts)
        self.assertEqual(set(names[len(starts):]) & {'2M2,A2', 'A1,A2', 'GIVEN,A2', 'A3,A2,A1'},
                         set(names[len(starts):]))
        self.assertEqual(len(names), len(set(names)), "no shade is listed twice")

    def test_the_limit_is_kept(self):
        self.assertEqual(len(self._names('A2', limit=3)), 3)

    def test_search_more_lists_the_shortest_match_first(self):
        """Search More shows the name_search hits in the model's order, not in
        name_search's: the shortest of them is the exact one."""
        ids = [i for i, _n in self.env['product.colour'].name_search('A2', limit=1000)]
        listed = self.env['product.colour'].search([('id', 'in', ids)])
        self.assertEqual(listed[0], self.a2)

    def test_a_percent_sign_is_not_a_wildcard(self):
        self.assertEqual(self._names('A%'), [])

    def test_another_spelling_of_an_existing_shade_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env['product.colour'].create({'name': 'a2, a3'})
        with self.assertRaises(ValidationError):
            self.a3.name = ' A2 '
        self.env['product.colour'].create({'name': 'A2,A3,B1'})   # a new combination is fine

    def test_an_order_line_offers_no_create(self):
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id, 'form')['arch']
        fields = etree.fromstring(arch).xpath(
            "//field[@name='order_line']//field[@name='color_scheme']")
        self.assertTrue(fields)
        for field in fields:
            self.assertIn("'no_create': True", field.get('options') or '')

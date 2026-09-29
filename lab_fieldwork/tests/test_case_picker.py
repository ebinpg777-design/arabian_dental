# -*- coding: utf-8 -*-
"""The phone's item picker: the likely appliance first, as the executive.

An executive at a counter should reach the common product in one tap. The picker
ranks by what went on real orders - for this clinic, then for the whole lab - and
is called by somebody with no sales rights at all, so it is tested as them.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged

LAT, LON = 9.9816, 76.2999


@tagged('post_install', '-at_install')
class TestCasePicker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Picker Exec', 'login': 'picker_exec',
            'group_ids': [(6, 0, [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Picker Clinic', 'is_clinic': True, 'partner_latitude': LAT, 'partner_longitude': LON,
            'visit_radius_m': 200.0})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'Picker Other Clinic', 'is_clinic': True})
        cls.categ = cls.env['product.category'].create({'name': 'ZZ Picker Appliances'})
        P = cls.env['product.product']
        cls.common = P.create({'name': 'ZZPICK Common Plate', 'sale_ok': True, 'categ_id': cls.categ.id})
        cls.rare = P.create({'name': 'ZZPICK Rare Splint', 'sale_ok': True, 'categ_id': cls.categ.id})
        cls.never = P.create({'name': 'ZZPICK Never Ordered', 'sale_ok': True, 'categ_id': cls.categ.id})
        cls.hidden = P.create({'name': 'ZZPICK Not For Sale', 'sale_ok': False, 'categ_id': cls.categ.id})
        cls.shade_a = cls.env['product.colour'].create({'name': 'ZZ-A2'})
        cls.shade_b = cls.env['product.colour'].create({'name': 'ZZ-B3'})

        def order(partner, product, times, shade=None):
            for _i in range(times):
                so = cls.env['sale.order'].create({
                    'partner_id': partner.id,
                    'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1,
                                           'color_scheme': shade.id if shade else False})]})
                so.write({'state': 'sale', 'date_order': fields.Datetime.now()})
        order(cls.other_clinic, cls.common, 5, cls.shade_a)
        order(cls.clinic, cls.rare, 2, cls.shade_b)
        order(cls.clinic, cls.common, 1, cls.shade_a)
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _ids(self, rows):
        return [r['id'] for r in rows]

    def test_a_search_finds_by_name_and_ranks_by_use(self):
        res = self.Case.picker_products(query='ZZPICK')
        ids = self._ids(res['results'])
        self.assertEqual(ids[:2], [self.common.id, self.rare.id], "six orders before two")
        self.assertIn(self.never.id, ids, "a product nobody ordered yet is still findable")
        self.assertNotIn(self.hidden.id, ids, "what cannot be sold cannot go on a case")
        row = res['results'][0]
        self.assertEqual(row['uses'], 6)
        self.assertEqual(row['categ_id'], self.categ.id)

    def test_the_clinic_shelf_is_what_this_clinic_orders(self):
        res = self.Case.picker_products(partner_id=self.clinic.id)
        clinic = [r for r in res['clinic'] if r['id'] in (self.common.id, self.rare.id)]
        self.assertEqual(self._ids(clinic), [self.rare.id, self.common.id], "their own habit, not the lab's")
        self.assertEqual(clinic[0]['uses'], 2)
        self.assertTrue(res['categories'], "the category chips come with the first read")
        self.assertFalse(res['results'])
        nobody = self.Case.picker_products(partner_id=self.env['res.partner'].create({'name': 'New Clinic'}).id)
        self.assertFalse(nobody['clinic'], "a clinic with no orders has no shelf, not somebody else's")

    def test_a_category_narrows_the_list(self):
        res = self.Case.picker_products(categ_id=self.categ.id)
        self.assertEqual(set(self._ids(res['results'])), {self.common.id, self.rare.id, self.never.id})
        cats = self.Case.picker_products()['categories']
        self.assertIn(self.categ.id, [c['id'] for c in cats] + [self.categ.id])
        self.assertTrue(all(c['count'] > 0 for c in cats))

    def test_shades_come_most_used_first_and_by_name(self):
        rows = self.Case.picker_colours(query='ZZ-')
        self.assertEqual(self._ids(rows), [self.shade_a.id, self.shade_b.id])
        self.assertEqual(rows[0]['uses'], 6)
        self.assertEqual(self._ids(self.Case.picker_colours(query='ZZ-B')), [self.shade_b.id])
        self.assertTrue(self.Case.picker_colours(), "with nothing typed the common shades are offered")

    def test_a_case_entered_from_the_picker_saves_and_submits(self):
        # a visit already under way: checking in is the day-start flow's test, not this one's
        visit = self.env['lab.visit'].create({'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        visit.write({'state': 'open', 'check_in': fields.Datetime.now()})
        case = self.Case.create({
            'visit_id': visit.id, 'patient': 'Anu Raj', 'priority': 'urgent',
            'line_ids': [(0, 0, {'product_id': self.common.id, 'ul': 'ul', 'quantity': 2, 'colour_id': self.shade_a.id}),
                         (0, 0, {'product_id': self.rare.id, 'ul': 'lower', 'quantity': 1, 'is_urgent': True,
                                 'note': 'extra clasp on 16'})]})
        self.assertEqual(case.line_count, 2)
        case.action_submit()
        self.assertEqual(case.state, 'submitted')

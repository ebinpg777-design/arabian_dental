# -*- coding: utf-8 -*-
"""Where a case is sent, and whose name goes on the box.

Most work returns to the clinic. Two exceptions carry a typed address instead: the
patient's own home, and a custom address that is neither - a hostel desk, a relative,
a courier counter. The custom one names NOBODY: the doctor's name identifies the
practice and the patient's name identifies who is being treated, and neither belongs
on a parcel handed to a stranger. (client, 2026-08-28)
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDeliveryDestination(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Destination Clinic', 'street': '12 Clinic Road', 'city': 'Kochi'})
        cls.product = cls.env['product.product'].create({
            'name': 'Destination Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id, 'patient': 'Ravi Menon',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]}, **vals))

    # ------------------------------------------------------------ the default
    def test_an_ordinary_case_carries_no_typed_address(self):
        order = self._order()
        self.assertEqual(order.lab_delivery_address_lines(), [])
        self.assertFalse(order.lab_delivery_is_anonymous())

    # ------------------------------------------------------------ custom address
    def test_a_custom_address_is_what_the_label_carries(self):
        order = self._order(deliver_to_custom=True,
                            custom_address='Sree Hostel, Room 14\nMG Road\nKochi 682016')
        self.assertEqual(order.lab_delivery_address_lines(),
                         ['Sree Hostel, Room 14', 'MG Road', 'Kochi 682016'])

    def test_a_custom_address_names_nobody(self):
        order = self._order(deliver_to_custom=True, custom_address='Sree Hostel')
        self.assertTrue(order.lab_delivery_is_anonymous())

    def test_the_patients_home_still_names_the_patient(self):
        order = self._order(deliver_to_patient=True, patient_address='7 Beach Road')
        self.assertEqual(order.lab_delivery_address_lines(), ['7 Beach Road'])
        self.assertFalse(order.lab_delivery_is_anonymous(),
                         "a parcel to the patient's own home may name them")

    def test_blank_lines_and_stray_spacing_are_tidied(self):
        order = self._order(deliver_to_custom=True,
                            custom_address='  Flat   9 \n\n   Marine   Drive  \n')
        self.assertEqual(order.lab_delivery_address_lines(),
                         ['Flat 9', 'Marine Drive'])

    # ------------------------------------------------------------ the rules
    def test_a_custom_destination_needs_an_address(self):
        with self.assertRaises(ValidationError):
            self._order(deliver_to_custom=True)

    def test_whitespace_is_not_an_address(self):
        with self.assertRaises(ValidationError):
            self._order(deliver_to_custom=True, custom_address='   \n  ')

    def test_a_case_cannot_go_to_two_places_at_once(self):
        with self.assertRaises(ValidationError):
            self._order(deliver_to_patient=True, patient_address='7 Beach Road',
                        deliver_to_custom=True, custom_address='Sree Hostel')

    def test_choosing_one_destination_clears_the_other(self):
        """The onchange is what the form runs; on a stored record the constraint
        fires on write before it could, which is the other half of the guard."""
        draft = self.env['sale.order'].new({
            'partner_id': self.clinic.id,
            'deliver_to_patient': True, 'patient_address': '7 Beach Road',
            'deliver_to_custom': True, 'custom_address': 'Sree Hostel'})
        draft._onchange_deliver_to_custom()
        self.assertFalse(draft.deliver_to_patient)
        self.assertFalse(draft.patient_address)
        self.assertTrue(draft.deliver_to_custom)

    def test_unticking_clears_the_address_so_it_cannot_be_printed_later(self):
        order = self._order(deliver_to_custom=True, custom_address='Sree Hostel')
        order.deliver_to_custom = False
        order._onchange_deliver_to_custom()
        self.assertFalse(order.custom_address)
        self.assertEqual(order.lab_delivery_address_lines(), [])

    def test_it_is_not_copied_onto_a_duplicated_order(self):
        order = self._order(deliver_to_custom=True, custom_address='Sree Hostel')
        copy = order.copy()
        self.assertFalse(copy.deliver_to_custom)
        self.assertFalse(copy.custom_address)

    # ------------------------------------------------------------ the label
    def test_the_sticker_prints_the_address_and_no_names(self):
        order = self._order(deliver_to_custom=True,
                            custom_address='Sree Hostel, Room 14\nKochi')
        order.action_confirm()
        engine = self.env['epg.sticker.engine']
        stickers = engine.build(order, {'mode': 'order', 'show_lines': False,
                                        'show_phone': False, 'show_route': False,
                                        'show_patient': True, 'copies': 1,
                                        'barcode_type': 'code128'})
        one = next(s for s in stickers if s.get('refs') == order.name)
        self.assertEqual(one['address'], ['Sree Hostel, Room 14', 'Kochi'])
        self.assertEqual(one['name'], '', "no doctor and no patient on the box")
        self.assertEqual(one['clinic'], '')
        self.assertEqual(one['patient'], '')

    def test_a_patient_sticker_still_names_the_patient(self):
        order = self._order(deliver_to_patient=True, patient_address='7 Beach Road')
        order.action_confirm()
        stickers = self.env['epg.sticker.engine'].build(order, {
            'mode': 'order', 'show_lines': False, 'show_phone': False,
            'show_route': False, 'show_patient': True, 'copies': 1,
            'barcode_type': 'code128'})
        one = next(s for s in stickers if s.get('refs') == order.name)
        self.assertEqual(one['name'], 'Ravi Menon')
        self.assertEqual(one['clinic'], 'Destination Clinic')

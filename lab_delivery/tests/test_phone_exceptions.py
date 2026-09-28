# -*- coding: utf-8 -*-
"""A hand-over by a phone excused from giving a position: recorded, and read as
'not required' rather than 'no location'."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPhoneExceptions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id
        executive = G('lab_fieldwork.group_fieldwork_executive').id
        cls.excused = cls.env['res.users'].create({
            'name': 'Excused Courier', 'login': 'excused_courier',
            'fw_location_exception': True,
            'group_ids': [(6, 0, [base, executive])]})
        cls.plain = cls.env['res.users'].create({
            'name': 'Plain Courier', 'login': 'plain_courier',
            'group_ids': [(6, 0, [base, executive])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Pinned Clinic', 'is_clinic': True,
            'partner_latitude': 9.9312, 'partner_longitude': 76.2673})
        cls.product = cls.env['product.product'].create({
            'name': 'Located Appliance', 'type': 'consu', 'list_price': 10.0})

    def _delivery(self, user):
        """Carried by that person: an executive may only write their own runs."""
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Located Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return self.env['lab.delivery'].create(
            {'sale_order_id': order.id, 'executive_id': user.id})

    def _hand_over_as(self, user, delivery):
        """The whole path as that person: the button's action context into the
        done wizard, which stamps them as the one who handed it over."""
        action = delivery.with_user(user).action_mark_delivered(False, False, False)
        wizard = self.env['lab.delivery.done.wizard'].with_user(user).with_context(
            **action['context']).create({'delivery_id': delivery.id,
                                         'delivery_outcome': 'clinic'})
        wizard.action_confirm()

    def test_an_excused_phone_hands_over_with_no_location_and_no_blame(self):
        delivery = self._delivery(self.excused)
        self._hand_over_as(self.excused, delivery)
        self.assertEqual(delivery.delivered_by_id, self.excused)
        self.assertEqual(delivery.delivered_gps_state, 'exempt')

    def test_a_plain_phone_that_gave_nothing_is_still_no_location(self):
        delivery = self._delivery(self.plain)
        self._hand_over_as(self.plain, delivery)
        self.assertEqual(delivery.delivered_by_id, self.plain)
        self.assertEqual(delivery.delivered_gps_state, 'nofix')

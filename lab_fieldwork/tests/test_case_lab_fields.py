# -*- coding: utf-8 -*-
"""What the lab's order asks for and the slip did not carry.

The order form of this lab has an appliance type it cannot be registered without, a
place the finished work goes, and - on every line - the teeth and the cast. None of
it was on the executive's slip, so the counter rang the clinic to ask. These hold the
slip to the order: what is entered at the clinic must arrive on the order, written the
way the order writes it."""
from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseLabFields(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Slip Exec', 'login': 'slip_exec',
            'group_ids': [(6, 0, [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Slip Clinic', 'is_clinic': True, 'partner_latitude': 9.9816, 'partner_longitude': 76.2999,
            'visit_radius_m': 200.0})
        cls.crown = cls.env['product.product'].create({'name': 'ZZ Slip Crown', 'sale_ok': True})
        cls.shade = cls.env['product.colour'].create({'name': 'ZZ-A3'})

    def _case(self, lines=None, **vals):
        visit = self.env['lab.visit'].create({'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        visit.write({'state': 'open', 'check_in': fields.Datetime.now()})
        return self.env['lab.case'].with_user(self.exec_user).create(dict({
            'visit_id': visit.id, 'patient': 'Slip Patient',
            'line_ids': lines or [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 1})],
        }, **vals))

    def _order(self, case):
        case.action_submit()
        if not case.sale_order_id:
            case.sudo().action_create_order()
        return case.sudo().sale_order_id

    def test_the_kind_of_appliance_reaches_the_order(self):
        for kind in ('fixed', 'removable', 'clear_retainer', 'other'):
            with self.subTest(kind=kind):
                order = self._order(self._case(appliance_type=kind, patient='Patient %s' % kind))
                self.assertEqual(order.appliance_type, kind)
        self.assertFalse(self._order(self._case(patient='Nobody Said')).appliance_type,
                         "unanswered stays unanswered: it is never guessed as Fixed")

    def test_the_slip_and_the_order_name_the_same_kinds(self):
        self.assertEqual(self.env['lab.case']._fields['appliance_type'].selection,
                         self.env['sale.order']._fields['appliance_type'].selection)
        self.assertEqual([k for k, _v in self.env['lab.case.line']._fields['cast'].selection],
                         [k for k, _v in self.env['sale.order.line']._fields['cast'].selection])

    def test_what_else_came_in_the_bag_reaches_the_order(self):
        order = self._order(self._case(is_3d_model_print=True, is_dd_cheque=True, is_others=True, is_screw=True))
        self.assertTrue(order.is_3d_model_print and order.is_dd_cheque and order.is_others and order.is_screw)
        plain = self._order(self._case(patient='Plain Bag'))
        self.assertFalse(plain.is_3d_model_print or plain.is_dd_cheque or plain.is_others)

    def test_the_teeth_and_the_cast_reach_the_order_line(self):
        case = self._case(lines=[
            (0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 3, 'teeth': '13, 11, 21',
                    'cast': 'ul', 'colour_id': self.shade.id}),
            (0, 0, {'product_id': self.crown.id, 'ul': 'lower', 'quantity': 1})])
        first, second = self._order(case).order_line.sorted('id')
        self.assertEqual((first.teeth, first.cast, first.ul, first.product_uom_qty), ('13, 11, 21', 'ul', 'upper', 3))
        self.assertEqual(first.color_scheme, self.shade)
        self.assertFalse(second.teeth)
        self.assertFalse(second.cast, "no cast said is no cast written")

    def test_the_work_goes_to_the_clinic_unless_the_slip_says_otherwise(self):
        order = self._order(self._case())
        self.assertFalse(order.deliver_to_patient or order.deliver_to_custom)
        self.assertFalse(order.patient_address or order.custom_address)

    def test_a_delivery_to_the_patient_is_written_the_orders_way(self):
        order = self._order(self._case(deliver_to='patient', delivery_address='12 Hill Road, Manjeri 676121. Ph 98470 00000'))
        self.assertTrue(order.deliver_to_patient)
        self.assertFalse(order.deliver_to_custom)
        self.assertIn('Hill Road', order.patient_address)
        self.assertFalse(order.custom_address)

    def test_a_delivery_to_another_address_is_written_the_orders_way(self):
        order = self._order(self._case(deliver_to='custom', delivery_address='Branch clinic, Nilambur'))
        self.assertTrue(order.deliver_to_custom)
        self.assertFalse(order.deliver_to_patient)
        self.assertEqual(order.custom_address, 'Branch clinic, Nilambur')
        self.assertFalse(order.patient_address)

    def test_somewhere_else_needs_its_address(self):
        for where in ('patient', 'custom'):
            for address in (False, '   '):
                with self.subTest(where=where, address=address):
                    case = self._case(deliver_to=where, delivery_address=address, patient='No Address %s' % where)
                    with self.assertRaises(UserError):
                        case.action_submit()
                    self.assertEqual(case.state, 'draft')

    def test_an_address_left_behind_is_not_carried_to_the_clinic(self):
        """Chosen 'the patient', address typed, then changed back to the clinic."""
        order = self._order(self._case(deliver_to='clinic', delivery_address='12 Hill Road'))
        self.assertFalse(order.deliver_to_patient or order.deliver_to_custom)
        self.assertFalse(order.patient_address or order.custom_address)

    def test_the_form_asks_for_what_the_order_cannot_do_without(self):
        arch = etree.fromstring(self.env['lab.case'].with_user(self.exec_user).get_view(view_type='form')['arch'])
        kind = arch.xpath("//field[@name='appliance_type']")[0]
        self.assertEqual(kind.get('required'), "state == 'draft'")
        self.assertEqual(kind.get('widget'), 'fw_choice')
        address = arch.xpath("//field[@name='delivery_address']")[0]
        self.assertIn("deliver_to != 'clinic'", address.get('required'))
        loaded = arch.xpath("//field[@name='line_ids']/list/field/@name")
        self.assertTrue({'teeth', 'cast', 'colour_id', 'ul', 'quantity'} <= set(loaded),
                        "the item cards can only show and write what the list loads")
        toggles = arch.xpath("//widget[@name='fw_toggles']/@fields")[0].split(',')
        self.assertTrue({'is_3d_model_print', 'is_dd_cheque', 'is_others'} <= set(toggles))
        self.assertEqual(len(toggles), len(arch.xpath("//widget[@name='fw_toggles']/@colors")[0].split(',')))

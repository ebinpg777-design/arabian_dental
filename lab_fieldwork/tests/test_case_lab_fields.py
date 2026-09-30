# -*- coding: utf-8 -*-
"""What the lab's order asks for and the slip did not carry.

The order form of this lab has a place the finished work goes and - on every line -
the teeth and the cast. None of
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

    def test_the_slip_asks_no_appliance_type(self):
        """An orthodontics question a dental lab has no answer to. (client, 2026-09-30)"""
        self.assertNotIn('appliance_type', self.env['lab.case']._fields)
        order = self._order(self._case(patient='No Kind Asked'))
        self.assertTrue(order, "a slip with no appliance type still makes its order")

    def test_the_slip_and_the_order_name_the_same_casts(self):
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
        self.assertFalse(arch.xpath("//field[@name='appliance_type']"))
        address = arch.xpath("//field[@name='delivery_address']")[0]
        self.assertIn("deliver_to != 'clinic'", address.get('required'))
        loaded = arch.xpath("//field[@name='line_ids']/list/field/@name")
        self.assertTrue({'teeth', 'cast', 'colour_id', 'ul', 'quantity'} <= set(loaded),
                        "the item cards can only show and write what the list loads")
        toggles = arch.xpath("//widget[@name='fw_toggles']/@fields")[0].split(',')
        self.assertTrue({'is_3d_model_print', 'is_dd_cheque', 'is_others'} <= set(toggles))
        self.assertEqual(len(toggles), len(arch.xpath("//widget[@name='fw_toggles']/@colors")[0].split(',')))

    def test_the_executive_profile_shows_the_phone_exceptions(self):
        """Field Roles opens the simplified user form; the boxes must be on it."""
        admin = self.env['res.users'].create({
            'name': 'Field Admin', 'login': 'slip_field_admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('lab_fieldwork.group_fieldwork_admin').id])]})
        action = self.env.ref('lab_fieldwork.action_fieldwork_users')
        form = etree.fromstring(self.env['res.users'].with_user(admin).get_view(view_type='form')['arch'])
        self.assertEqual({f.get('name') for f in form.xpath("//field[@name='fw_location_exception' or @name='fw_camera_exception']")},
                         {'fw_location_exception', 'fw_camera_exception'})
        listed = action.view_ids.filtered(lambda v: v.view_mode == 'list').view_id
        arch = etree.fromstring(self.env['res.users'].with_user(admin).get_view(listed.id, view_type='list')['arch'])
        self.assertTrue(arch.xpath("//field[@name='fw_location_exception']") and arch.xpath("//field[@name='fw_camera_exception']"))
        # an executive sees neither: the exception is set for them, not by them
        mine = etree.fromstring(self.env['res.users'].with_user(self.exec_user).get_view(view_type='form')['arch'])
        self.assertFalse(mine.xpath("//field[@name='fw_location_exception']"))

    def test_a_field_admin_switches_an_exception_without_the_user_manager_right(self):
        from odoo.exceptions import AccessError
        admin = self.env['res.users'].create({
            'name': 'Field Admin Two', 'login': 'slip_field_admin2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('lab_fieldwork.group_fieldwork_admin').id])]})
        self.assertFalse(admin.has_group('base.group_erp_manager'), "the point: this person cannot edit users")
        target = self.exec_user.with_user(admin)
        target.with_context(fw_exception='location', fw_value=True).action_fw_set_exception()
        target.with_context(fw_exception='camera', fw_value=True).action_fw_set_exception()
        self.assertEqual(self.exec_user._fw_phone_exceptions(), {'location': True, 'camera': True})
        self.assertIn('switched on by Field Admin Two', ' '.join(self.exec_user.partner_id.message_ids.mapped('body')))
        target.with_context(fw_exception='camera', fw_value=False).action_fw_set_exception()
        self.assertEqual(self.exec_user._fw_phone_exceptions(), {'location': True, 'camera': False})
        with self.assertRaises(AccessError):
            self.exec_user.with_user(self.exec_user).with_context(fw_exception='location', fw_value=False).action_fw_set_exception()
        with self.assertRaises(UserError):
            target.with_context(fw_exception='microphone', fw_value=True).action_fw_set_exception()
        self.assertTrue(self.exec_user.fw_location_exception, "an executive cannot switch their own exception off")

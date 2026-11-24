# -*- coding: utf-8 -*-
"""Cases the executive registers without a visit, cases with nothing listed, shades
added from a case, and the person who must contact the doctor. (client, 2026-11-24)"""
from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseWithoutVisit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Post Exec', 'login': 'post_exec', 'group_ids': [(6, 0, groups)]})
        cls.colleague = cls.env['res.users'].create({
            'name': 'Post Colleague', 'login': 'post_colleague', 'group_ids': [(6, 0, groups)]})
        cls.office = cls.env['res.users'].create({
            'name': 'Post Office', 'login': 'post_office',
            'group_ids': [(6, 0, [G('base.group_user').id])]})
        cls.contact = cls.env['hr.employee'].create({'name': 'Post Contact', 'user_id': cls.office.id})
        # On the executive's own route: those are the clinics the case form offers
        # them, and the only ones they can read.
        route = cls.env['crm.team'].create({'name': 'Post Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Post Clinic', 'is_clinic': True, 'team_id': route.id})
        cls.crown = cls.env['product.product'].create({'name': 'ZZ Post Crown', 'sale_ok': True})
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _sent(self, **vals):
        return self.Case.create(dict({
            'received_via': 'courier', 'partner_id': self.clinic.id, 'patient': 'Post Patient',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 1})],
        }, **vals))

    # ------------------------------------------------------------ no visit
    def test_a_case_by_courier_needs_no_visit_and_fills_in_its_own_day(self):
        case = self._sent(received_ref='DTDC 7781')
        self.assertFalse(case.visit_id)
        self.assertEqual(case.user_id, self.exec_user)
        self.assertEqual(case.date, fields.Date.context_today(case))
        self.assertEqual(case.company_id, self.env.company)
        self.assertEqual(case.partner_id, self.clinic)

    def test_submitting_it_makes_the_order_at_once(self):
        """There is no check-out to wait for."""
        case = self._sent(received_via='transport', received_ref='LR 12')
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertTrue(order, "no visit to finish, so the order comes on submit")
        self.assertEqual(case.state, 'registered')
        self.assertEqual(order.case_received_via, 'transport')
        self.assertEqual(order.case_received_ref, 'LR 12')
        self.assertEqual(order.verification_state, 'to_verify')
        # the order's number on the slip, readable by the executive who cannot read orders
        case.invalidate_recordset()
        self.assertEqual(case.with_user(self.exec_user).read(['order_name'])[0]['order_name'], order.name)
        self.assertTrue(order._verification_origin_requires(),
                        "work from the field is checked, visit or no visit")

    def test_without_a_visit_it_must_say_courier_or_transport(self):
        with self.assertRaises(ValidationError):
            self._sent(received_via='visit')

    def test_a_case_at_a_visit_still_takes_everything_from_the_visit(self):
        visit = self.env['lab.visit'].create({'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        visit.write({'state': 'open', 'check_in': fields.Datetime.now()})
        case = self.Case.create({
            'visit_id': visit.id, 'patient': 'At The Counter',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'lower', 'quantity': 1})]})
        self.assertEqual(case.received_via, 'visit')
        self.assertEqual((case.partner_id, case.user_id, case.date), (visit.partner_id, visit.user_id, visit.date))

    def test_an_executive_cannot_register_one_in_a_colleagues_name(self):
        with self.assertRaises(UserError):
            self._sent(user_id=self.colleague.id)
        case = self._sent()
        with self.assertRaises(UserError):
            case.user_id = self.colleague

    def test_it_counts_in_the_executives_day(self):
        case = self._sent()
        case.action_submit()
        day = self.env['lab.case']._without_visit([self.exec_user.id], case.date)[self.exec_user.id]
        self.assertEqual((day['cases'], day['orders']), (1, 1))

    # ------------------------------------------------------------ nothing listed
    def test_a_case_with_nothing_listed_is_saved_with_the_doctor_call_on(self):
        case = self._sent(line_ids=[])
        self.assertTrue(case.needs_doctor_call)
        self.assertTrue(case.doctor_call_note, "the desk is told what to ask")
        case.needs_doctor_call = False
        self.assertTrue(case.needs_doctor_call, "while it is empty, the call stays on")

    def test_taking_the_last_item_off_turns_the_call_on(self):
        case = self._sent()
        self.assertFalse(case.needs_doctor_call)
        case.line_ids.unlink()
        self.assertTrue(case.needs_doctor_call)

    def test_an_empty_case_reaches_the_queue_for_the_call_and_cannot_be_verified_empty(self):
        case = self._sent(line_ids=[])
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertTrue(order)
        self.assertFalse(order.order_line)
        self.assertEqual(order.verification_state, 'to_verify')
        self.assertTrue(order.call_doctor_required)
        with self.assertRaises(UserError):
            order.sudo().action_verify()

    # ------------------------------------------------------------ shades
    def test_an_executive_can_add_a_shade_from_the_case(self):
        made = self.Case.picker_add_colour('  ZZ 9Q9 ')
        self.assertTrue(made['created'])
        shade = self.env['product.colour'].browse(made['id'])
        self.assertEqual(shade.name, 'ZZ 9Q9')
        again = self.Case.picker_add_colour('zz9q9')
        self.assertEqual(again['id'], shade.id, "another spelling is the same shade")
        self.assertFalse(again['created'])

    def test_only_field_users_add_shades(self):
        with self.assertRaises(UserError):
            self.env['lab.case'].with_user(self.office).picker_add_colour('ZZ 8Q8')

    # ------------------------------------------------------------ contact person
    def test_an_executive_can_pick_the_contact_person(self):
        """From the public employee list: hr.employee itself is closed to them, and the
        phone's picker refused them before."""
        found = self.env['hr.employee.public'].with_user(self.exec_user).name_search('Post Contact')
        self.assertIn(self.contact.id, [rid for rid, _name in found])

    def test_the_contact_person_goes_to_the_order_with_a_to_do(self):
        case = self._sent(contact_employee_id=self.contact.id, contact_note='Doctor talks only to her')
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertEqual(order.case_contact_employee_id.id, self.contact.id)
        self.assertEqual(order.case_contact_note, 'Doctor talks only to her')
        todo = order.activity_ids.filtered(lambda a: a.user_id == self.office)
        self.assertTrue(todo, "the contact person has it in their own activities")

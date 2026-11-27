# -*- coding: utf-8 -*-
"""Submitting a case makes its order; and a case has a shade of its own.

(client, 2026-11-25: "when submitting it will create order; when editing, the
modification will come in the SO linked in the case" and "field executive can add
shades in the case register view instead of items; do not remove shade in items;
the shades in the case come in all items; if a shade change is needed for a specific
item he can change it in the item")
"""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseShadeAndSubmit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Shade Exec', 'login': 'shade_exec', 'group_ids': [(6, 0, groups)]})
        route = cls.env['crm.team'].create({'name': 'Shade Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Shade Clinic', 'is_clinic': True, 'team_id': route.id})
        cls.crown = cls.env['product.product'].create({'name': 'ZZ Shade Crown', 'sale_ok': True,
                                                       'list_price': 1000})
        cls.bridge = cls.env['product.product'].create({'name': 'ZZ Shade Bridge', 'sale_ok': True,
                                                        'list_price': 3000})
        Colour = cls.env['product.colour']
        cls.a2 = Colour.create({'name': 'ZZ-SH-A2'})
        cls.a3 = Colour.create({'name': 'ZZ-SH-A3'})
        cls.b1 = Colour.create({'name': 'ZZ-SH-B1'})
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _visit(self):
        visit = self.env['lab.visit'].create({'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        visit.write({'state': 'open', 'check_in': fields.Datetime.now()})
        return visit

    def _case(self, **vals):
        return self.Case.create(dict({
            'received_via': 'courier', 'partner_id': self.clinic.id, 'patient': 'Shade Patient',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 1}),
                         (0, 0, {'product_id': self.bridge.id, 'ul': 'lower', 'quantity': 3})],
        }, **vals))

    # ---------------------------------------------------------- order on submit
    def test_a_case_on_an_open_visit_is_an_order_as_soon_as_it_is_submitted(self):
        visit = self._visit()
        case = self.Case.create({
            'visit_id': visit.id, 'patient': 'Open Visit Patient',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 1})]})
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertEqual(case.state, 'registered')
        self.assertTrue(order, "no waiting for the visit to be finished")
        self.assertEqual(order.verification_state, 'to_verify')
        self.assertEqual(visit.state, 'open')
        self.assertIn(order.name, visit.message_ids[0].body, "the visit says which order")

    def test_a_slip_left_half_entered_no_longer_holds_the_others_back(self):
        """The visit that started this: one slip still being entered refuses the
        check-out, and two submitted cases sat without an order behind it."""
        visit = self._visit()
        visit.outcome = 'order'
        draft = self.Case.create({'visit_id': visit.id, 'patient': 'Half Entered',
                                  'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper'})]})
        done = self.Case.create({'visit_id': visit.id, 'patient': 'Finished One',
                                 'line_ids': [(0, 0, {'product_id': self.bridge.id, 'ul': 'lower'})]})
        done.action_submit()
        self.assertTrue(done.sudo().sale_order_id)
        with self.assertRaises(UserError):
            visit.with_context(fw_auto_close=True).do_check_out()
        self.assertEqual(draft.state, 'draft', "the unfinished slip is still the executive's to finish")
        self.assertEqual(visit.sudo().order_count, 1)

    def test_a_case_submitted_before_the_rule_is_ordered_when_the_visit_is_finished(self):
        visit = self._visit()
        visit.outcome = 'order'
        case = self.Case.create({'visit_id': visit.id, 'patient': 'Old Rule',
                                 'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper'})]})
        case.sudo().write({'state': 'submitted'})       # as it stood before 2026-11-25
        self.assertFalse(case.sudo().sale_order_id)
        visit.with_context(fw_auto_close=True).do_check_out()
        self.assertEqual(case.state, 'registered')
        self.assertTrue(case.sudo().sale_order_id)

    def test_an_edited_case_changes_its_own_order(self):
        case = self._case()
        case.action_submit()
        order = case.sudo().sale_order_id
        case.action_reset_to_draft()
        case.write({'patient': 'Shade Patient Corrected', 'colour_id': self.a3.id})
        case.action_submit()
        self.assertEqual(case.sudo().sale_order_id, order, "the same order, not a second one")
        self.assertEqual(order.patient, 'Shade Patient Corrected')
        self.assertEqual(order.color_scheme, self.a3)
        self.assertEqual(order.verification_state, 'to_verify')

    # ------------------------------------------------------------ the case's shade
    def test_the_shade_of_the_case_goes_on_every_item(self):
        case = self._case(colour_id=self.a2.id)
        self.assertEqual(case.line_ids.mapped('colour_id'), self.a2)
        # an item added later takes it too
        case.write({'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'lower', 'quantity': 1})]})
        self.assertEqual(case.line_ids.mapped('colour_id'), self.a2)
        self.assertEqual(len(case.line_ids), 3)

    def test_an_item_keeps_a_shade_of_its_own(self):
        case = self._case(colour_id=self.a2.id)
        crown, bridge = case.line_ids
        bridge.colour_id = self.b1.id
        # the doctor changes the shade of the case: the crown follows, the bridge
        # was given its own and keeps it
        case.colour_id = self.a3.id
        self.assertEqual((crown.colour_id, bridge.colour_id), (self.a3, self.b1))
        # taken off the case: off the items that carried it, not off the bridge
        case.colour_id = False
        self.assertEqual((crown.colour_id, bridge.colour_id), (self.env['product.colour'], self.b1))

    def test_a_shade_given_on_the_case_later_fills_the_items_without_one(self):
        case = self._case()
        crown, bridge = case.line_ids
        self.assertFalse(crown.colour_id | bridge.colour_id)
        bridge.colour_id = self.b1.id
        case.colour_id = self.a2.id
        self.assertEqual((crown.colour_id, bridge.colour_id), (self.a2, self.b1))

    def test_the_order_carries_the_case_shade_and_each_items_own(self):
        case = self._case(colour_id=self.a2.id)
        case.line_ids[1].colour_id = self.b1.id
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertEqual(order.color_scheme, self.a2)
        lines = order.order_line.filtered(lambda l: not l.display_type and not l.is_urgent_service).sorted('id')
        self.assertEqual(lines.mapped('color_scheme'), self.a2 | self.b1)
        self.assertEqual(lines[0].color_scheme, self.a2)
        self.assertEqual(lines[1].color_scheme, self.b1)

    # ---------------------------------------------------- another case, same clinic
    def test_another_case_on_the_same_visit(self):
        visit = self._visit()
        case = self.Case.create({'visit_id': visit.id, 'patient': 'First Of Two',
                                 'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper'})]})
        case.action_submit()
        action = case.action_another_case()
        self.assertEqual(action['res_model'], 'lab.case')
        self.assertEqual(action['context'], {'default_visit_id': visit.id})
        second = self.Case.with_context(**action['context']).create({'patient': 'Second Of Two'})
        self.assertEqual((second.visit_id, second.partner_id), (visit, self.clinic))
        self.assertEqual(second.state, 'draft')

    def test_another_case_that_came_by_courier_keeps_the_clinic_and_the_docket(self):
        case = self._case(received_via='transport', received_ref='LR-4471')
        case.action_submit()
        context = case.action_another_case()['context']
        self.assertFalse(context.get('default_visit_id'))
        second = self.Case.with_context(**context).create({'patient': 'Same Parcel'})
        self.assertEqual((second.partner_id, second.received_via, second.received_ref),
                         (self.clinic, 'transport', 'LR-4471'))
        self.assertFalse(second.visit_id)

    def test_another_case_after_a_visit_that_was_cancelled_comes_without_it(self):
        visit = self._visit()
        case = self.Case.create({'visit_id': visit.id, 'patient': 'Visit Gone',
                                 'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper'})]})
        visit.sudo().write({'state': 'cancel'})
        context = case.action_another_case()['context']
        self.assertEqual(context.get('default_partner_id'), self.clinic.id)
        # no visit to take it to: collected at the clinic, the default since 2026-11-27
        self.assertEqual(context.get('default_received_via'), 'clinic')
        self.assertNotIn('default_visit_id', context)

    # ---------------------------------------------- what came with the case
    def test_what_came_with_the_case_goes_to_the_order(self):
        """Impression, tray, wax bite and the two models: taken on the case, carried to
        the order the production sheet prints from. (client, 2026-11-26)"""
        case = self._case(impression_type='Cast', impression_tray='Both', wax_bite='Yes',
                          opposing_model='Yes', pre_cutting_model='No')
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertEqual(order.wax_bite, 'Yes')
        self.assertEqual(order.impression_tray, 'Both')
        if 'opposing_model' in order._fields:       # the production sheet's fields
            self.assertEqual((order.impression_type, order.opposing_model, order.pre_cutting_model),
                             ('Cast', 'Yes', 'No'))
        # corrected from the field: the same order changes
        case.action_reset_to_draft()
        case.write({'opposing_model': 'No', 'impression_type': 'Rubber Base'})
        case.action_submit()
        self.assertEqual(order.impression_type, 'Rubber Base')
        if 'opposing_model' in order._fields:
            self.assertEqual(order.opposing_model, 'No')

    # ------------------------------------------------- Clinic, and the clinic picker
    def test_a_case_without_a_visit_is_collected_at_the_clinic_by_default(self):
        """(client, 2026-11-27: "Add 'Clinic' option in How it came and set it by default")"""
        case = self.Case.create({'partner_id': self.clinic.id, 'patient': 'At The Door'})
        self.assertEqual(case.received_via, 'clinic')
        case.action_submit()
        self.assertEqual(case.sudo().sale_order_id.case_received_via, 'clinic')

    def test_the_picker_offers_starred_and_recent_clinics_and_finds_by_place(self):
        """(client, 2026-11-27: "an attractive easy widget for adding Clinic/Doctor")"""
        other = self.env['res.partner'].create({
            'name': 'ZZ Picker Dental', 'is_clinic': True, 'city': 'Picker Town',
            'phone': '9000011111', 'team_id': self.clinic.team_id.id})
        Case = self.Case
        self.env['lab.clinic.favourite'].with_user(self.exec_user).toggle(other.id)
        Case.create({'partner_id': self.clinic.id, 'patient': 'Recent One'})
        data = Case.clinic_picker('')
        self.assertIn(other.id, [r['id'] for r in data['starred']])
        self.assertIn(self.clinic.id, [r['id'] for r in data['recent']])
        self.assertNotIn(other.id, [r['id'] for r in data['recent']], "a starred clinic is not listed twice")
        found = Case.clinic_picker('picker town')['results']
        self.assertEqual([r['id'] for r in found], [other.id])
        self.assertEqual((found[0]['place'], found[0]['phone'], found[0]['starred']),
                         ('Picker Town', '9000011111', True))
        stranger = self.env['res.partner'].create({'name': 'ZZ Picker Elsewhere', 'is_clinic': True})
        self.assertNotIn(stranger.id, [r['id'] for r in Case.clinic_picker('ZZ Picker')['results']],
                         "only the clinics of the executive's own routes")

    # ---------------------------------------------------- the jaw, and its teeth
    def test_an_item_has_no_jaw_until_one_is_chosen_and_submit_asks_for_it(self):
        """A pre-filled Upper was left on lower work. (client, 2026-11-27: "Cast and
        Jaws are some times mistakenly entered by executive")"""
        case = self.Case.create({'partner_id': self.clinic.id, 'patient': 'No Jaw',
                                 'line_ids': [(0, 0, {'product_id': self.crown.id})]})
        self.assertFalse(case.line_ids.ul, "nothing chosen for them")
        with self.assertRaises(UserError) as caught:
            case.action_submit()
        self.assertIn('tap the jaw', str(caught.exception))
        case.line_ids.ul = 'lower'
        case.action_submit()
        self.assertEqual(case.sudo().sale_order_id.order_line.ul, 'lower')

    def test_teeth_of_the_other_jaw_are_refused_by_name(self):
        case = self.Case.create({'partner_id': self.clinic.id, 'patient': 'Wrong Jaw',
                                 'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper',
                                                      'teeth': '14, 36'})]})
        with self.assertRaises(UserError) as caught:
            case.action_submit()
        self.assertIn('36', str(caught.exception))
        self.assertNotIn('14,', str(caught.exception))
        case.line_ids.ul = 'ul'
        case.action_submit()
        self.assertTrue(case.sudo().sale_order_id, "both jaws hold both teeth")


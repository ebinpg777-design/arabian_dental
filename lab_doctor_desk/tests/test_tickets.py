# -*- coding: utf-8 -*-
"""The doctor call desk, as the people who use it.

Every rule that matters is a rule about WHO: the desk takes tickets, a lead decides,
an executive answers only what was handed to them, and nobody opens the gate by
unticking a box. So each is tested as that person - a superuser would pass them all
and prove nothing."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

OPEN = ('new', 'taken', 'waiting', 'field')


@tagged('post_install', '-at_install')
class TestDoctorDesk(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.company = cls.env.company
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('lab_order_control.require_order_verification', 'False')
        params.set_param('lab_order_control.doctor_call_attempts_before_hold', '0')
        Users = cls.env['res.users'].with_context(no_reset_password=True)

        def user(name, login, *groups):
            return Users.create({'name': name, 'login': login,
                                 'group_ids': [(6, 0, [G('base.group_user').id] + [G(g).id for g in groups])]})
        # the desk role and nothing else: no sales, no accounting
        cls.agent = user('Desk Asha', 'ddk_agent', 'lab_doctor_desk.group_doctor_desk_agent')
        cls.agent2 = user('Desk Biju', 'ddk_agent2', 'lab_doctor_desk.group_doctor_desk_agent')
        cls.lead = user('Desk Lead', 'ddk_lead', 'lab_doctor_desk.group_doctor_desk_lead')
        cls.executive = user('Exec Rahul', 'ddk_exec', 'lab_fieldwork.group_fieldwork_executive')
        cls.seller = user('Seller Sini', 'ddk_seller', 'sales_team.group_sale_salesman')
        cls.nobody = user('Somebody Else', 'ddk_nobody')
        # switched on once there is somebody to sit at the desk: it refuses otherwise
        cls.company.write({
            'doctor_tickets': True, 'doctor_ticket_assign': 'pool', 'doctor_ticket_hours': 24.0,
            'doctor_ticket_rush_hours': 4.0, 'doctor_ticket_retry': 45, 'doctor_ticket_escalate': 3,
            'doctor_ticket_confirm': False})
        cls.clinic = cls.env['res.partner'].create({'name': 'DDK Smile Clinic', 'phone': '+91 483 276 0000'})
        cls.doctor = cls.env['res.partner'].create({
            'name': 'Dr. DDK Nair', 'parent_id': cls.clinic.id, 'type': 'contact', 'phone': '+91 98470 11111'})
        if 'whatsapp_number' in cls.doctor._fields:
            cls.doctor.whatsapp_number = '+91 98470 22222'
        cls.product = cls.env['product.product'].create({'name': 'DDK Crown', 'type': 'consu', 'list_price': 900.0})
        cls.Ticket = cls.env['lab.doctor.ticket']

    # ------------------------------------------------------------------ helpers
    def _order(self, call=True, partner=None, **vals):
        values = {'partner_id': (partner or self.doctor).id, 'patient': 'Binu Thomas', 'user_id': self.seller.id,
                  'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]}
        if call:
            values.update(call_doctor_required=True, call_doctor_reason='shade_confirmation',
                          call_doctor_note='Confirm the shade on 21')
        values.update(vals)
        return self.env['sale.order'].with_user(self.seller).sudo().create(values)

    def _ticket(self, **vals):
        order = self._order(**vals)
        ticket = order.doctor_ticket_ids
        self.assertEqual(len(ticket), 1)
        return order, ticket

    def _as(self, user, ticket):
        return ticket.with_user(user)

    def _told(self, ticket, user):
        """What `user` was notified of about `ticket`. Notifications are messages of their
        own kind and are not among a record's `message_ids`."""
        return self.env['mail.message'].sudo().search([
            ('model', '=', 'lab.doctor.ticket'), ('res_id', '=', ticket.id),
            ('message_type', '=', 'user_notification'), ('notified_partner_ids', 'in', user.partner_id.ids)])

    # ------------------------------------------------------------------ the switch
    def test_switched_off_nothing_is_raised_and_nothing_waits(self):
        self.company.doctor_tickets = False
        order = self._order()
        self.assertFalse(order.doctor_ticket_ids)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_switching_on_raises_what_was_already_waiting(self):
        self.company.doctor_tickets = False
        waiting, plain = self._order(), self._order(call=False)
        self.assertFalse(waiting.doctor_ticket_ids)
        settings = self.env['res.config.settings'].create({})
        self.assertGreaterEqual(settings.doctor_ticket_waiting, 1)
        self.company.doctor_tickets = True
        self.assertEqual(len(waiting.doctor_ticket_ids), 1, "raised by the switch itself")
        self.assertFalse(plain.doctor_ticket_ids)
        self.company._doctor_raise_waiting()
        self.assertEqual(len(waiting.doctor_ticket_ids), 1, "switching on twice raises nothing twice")

    def test_it_cannot_be_switched_on_with_nobody_at_the_desk(self):
        self.company.doctor_tickets = False
        desk = self.env.ref('lab_doctor_desk.group_doctor_desk_agent')
        (self.agent | self.agent2 | self.lead).write({'group_ids': [(3, desk.id), (3, self.env.ref('lab_doctor_desk.group_doctor_desk_lead').id)]})
        holders = desk.sudo().all_user_ids.filtered(lambda u: u.active and self.company in u.company_ids)
        holders.write({'active': False})       # whoever holds it through another role, on this database
        with self.assertRaises(UserError):
            self.company.doctor_tickets = True
        with self.assertRaises(UserError):
            self.env['res.config.settings'].create({'doctor_tickets': True})
        self.assertFalse(self.company.doctor_tickets)
        self.agent.write({'active': True, 'group_ids': [(4, desk.id)]})
        self.company.doctor_tickets = True
        self.assertTrue(self.company.doctor_tickets)

    # ------------------------------------------------------------------ raising
    def test_a_case_that_needs_the_doctor_raises_its_ticket(self):
        order, ticket = self._ticket()
        self.assertEqual(ticket.state, 'new')
        self.assertTrue(ticket.name.startswith('DCT/'))
        self.assertEqual(ticket.reason, 'shade_confirmation')
        self.assertEqual(ticket.question, 'Confirm the shade on 21')
        self.assertEqual(ticket.partner_id, self.doctor)
        self.assertEqual(ticket.doctor_id, self.clinic, "every question for the practice can be settled in one call")
        self.assertEqual(ticket.patient, 'Binu Thomas')
        self.assertEqual(ticket.phone, '+91 98470 11111')
        self.assertEqual(ticket.salesperson_id, self.seller)
        self.assertFalse(ticket.agent_id)
        hours = (ticket.deadline - ticket.raised_on).total_seconds() / 3600.0
        self.assertAlmostEqual(hours, 24.0, 1)
        self.assertIn(ticket.name, ' '.join(order.message_ids.mapped('body')))

    def test_a_rush_case_is_given_less_time(self):
        for priority, expected, mark in (('urgent', 4.0, '1'), ('emergency', 2.0, '2'), ('normal', 24.0, '0')):
            with self.subTest(priority=priority):
                order, ticket = self._ticket(priority=priority)
                self.assertEqual(ticket.priority, mark)
                self.assertAlmostEqual((ticket.deadline - ticket.raised_on).total_seconds() / 3600.0, expected, 1)

    def test_the_older_flag_raises_a_ticket_too(self):
        order = self._order(call=False)
        self.assertFalse(order.doctor_ticket_ids)
        order.with_user(self.seller).sudo().write({'is_pending_work': True})
        self.assertEqual(len(order.doctor_ticket_ids), 1)
        self.assertTrue(order.call_doctor_required)
        self.assertTrue(order.call_pending)

    def test_an_order_has_one_open_ticket_not_one_per_save(self):
        order, ticket = self._ticket()
        order.write({'call_doctor_required': True})
        order.write({'call_doctor_note': 'Confirm the shade on 21 and 22'})
        self.assertEqual(order.doctor_ticket_ids, ticket)
        self.assertEqual(ticket.question, 'Confirm the shade on 21 and 22', "a question put better before anybody rang")

    def test_the_question_is_not_rewritten_once_somebody_has_rung(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).log_call('no_answer')
        order.write({'call_doctor_note': 'Something else entirely'})
        self.assertEqual(ticket.question, 'Confirm the shade on 21')

    # ------------------------------------------------------------------ the gate
    def test_an_order_waits_for_its_ticket(self):
        order, ticket = self._ticket()
        with self.assertRaises(UserError) as gate:
            order.action_confirm()
        self.assertIn(ticket.name, str(gate.exception))
        self.assertEqual(order.state, 'draft')
        self._as(self.agent, ticket).action_answer('as_is', 'A2, as written')
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_the_gate_is_not_opened_by_unticking_a_box(self):
        order, ticket = self._ticket()
        for vals in ({'call_doctor_required': False}, {'call_doctor_resolved': True}, {'is_pending_work': False}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                order.with_user(self.seller).write(vals)
        with self.assertRaises(UserError):
            order.with_user(self.seller).action_resolve_doctor_call()
        self.assertEqual(ticket.state, 'new')
        self.assertTrue(order.call_pending)

    def test_a_lead_taking_the_call_off_withdraws_the_ticket_in_the_open(self):
        order, ticket = self._ticket()
        lead = self.lead
        lead.group_ids = [(4, self.env.ref('sales_team.group_sale_salesman_all_leads').id)]
        order.with_user(lead).write({'call_doctor_required': False})
        self.assertEqual(ticket.state, 'cancelled')
        self.assertIn('Desk Lead', ' '.join(ticket.message_ids.mapped('body')))
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_cancelling_the_order_withdraws_its_ticket(self):
        order, ticket = self._ticket()
        order._action_cancel()
        self.assertEqual(ticket.state, 'cancelled')
        other, second = self._ticket()
        other.write({'state': 'cancel'})
        self.assertEqual(second.state, 'cancelled', "a state written straight to cancel is a cancel too")

    # ------------------------------------------------------------------ at the desk
    def test_the_desk_takes_a_ticket_and_nobody_else_does(self):
        order, ticket = self._ticket()
        for outsider in (self.nobody, self.seller, self.executive):
            with self.subTest(user=outsider.name), self.assertRaises(AccessError):
                self._as(outsider, ticket).action_take()
        self._as(self.agent, ticket).action_take()
        self.assertEqual((ticket.state, ticket.agent_id), ('taken', self.agent))
        self.assertTrue(ticket.taken_on)
        with self.assertRaises(UserError):
            self._as(self.agent2, ticket).action_take()
        self._as(self.lead, ticket).action_take()
        self.assertEqual(ticket.agent_id, self.lead, "a lead may take what is somebody else's")

    def test_a_ticket_is_released_by_whoever_holds_it(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_take()
        with self.assertRaises(UserError):
            self._as(self.agent2, ticket).action_release()
        self._as(self.agent, ticket).action_release()
        self.assertEqual((ticket.state, ticket.agent_id.id), ('new', False))

    def test_a_lead_gives_a_ticket_to_somebody_at_the_desk(self):
        order, ticket = self._ticket()
        with self.assertRaises(AccessError):
            self._as(self.agent, ticket).action_assign(self.agent2.id)
        with self.assertRaises(UserError):
            self._as(self.lead, ticket).action_assign(self.nobody.id)
        self._as(self.lead, ticket).action_assign(self.agent2.id)
        self.assertEqual((ticket.state, ticket.agent_id), ('taken', self.agent2))

    def test_an_unanswered_call_waits_and_comes_back(self):
        order, ticket = self._ticket()
        before = fields.Datetime.now()
        self._as(self.agent, ticket).log_call('busy', remarks='Reception said he is in surgery')
        self.assertEqual(ticket.state, 'waiting')
        self.assertEqual(ticket.agent_id, self.agent, "whoever rang holds it")
        self.assertEqual((ticket.attempts, ticket.unanswered, ticket.last_response), (1, 1, 'busy'))
        wait = (ticket.callback_at - before).total_seconds() / 60.0
        self.assertAlmostEqual(wait, 45.0, 0)
        self.assertFalse(ticket.callback_due)
        call = ticket.call_ids
        self.assertEqual((call.called_by_id, call.order_id, call.channel), (self.agent, order, 'phone'))
        self.assertTrue(ticket.first_call_on)
        ticket.callback_at = fields.Datetime.now() - timedelta(minutes=1)
        ticket.invalidate_recordset()
        self.assertTrue(ticket.callback_due)

    def test_a_callback_needs_its_time(self):
        order, ticket = self._ticket()
        with self.assertRaises(UserError):
            self._as(self.agent, ticket).log_call('callback_requested')
        when = fields.Datetime.now() + timedelta(hours=3)
        self._as(self.agent, ticket).log_call('callback_requested', callback_at=fields.Datetime.to_string(when))
        self.assertEqual(ticket.callback_at, when.replace(microsecond=0))
        with self.assertRaises(UserError):
            self._as(self.agent, ticket).log_call('answered')
        with self.assertRaises(UserError):
            self._as(self.agent, ticket).log_call('shouted')

    def test_unanswered_attempts_escalate_once(self):
        order, ticket = self._ticket()
        desk = self._as(self.agent, ticket)
        desk.log_call('no_answer')
        desk.log_call('busy')
        self.assertFalse(ticket.escalated)
        desk.log_call('switched_off')
        self.assertTrue(ticket.escalated)
        self.assertIn('3', ticket.escalation_note)
        self.assertEqual(len(self._told(ticket, self.lead)), 1)
        desk.log_call('no_answer')
        self.assertEqual(len(self._told(ticket, self.lead)), 1, "once, not at every attempt after it")
        with self.assertRaises(AccessError):
            desk.action_settle_escalation()
        self._as(self.lead, ticket).action_settle_escalation()
        self.assertFalse(ticket.escalated)

    # ------------------------------------------------------------------ the answer
    def test_the_answer_closes_the_ticket_and_reaches_the_work(self):
        order, ticket = self._ticket()
        order.instruction = 'Handle with care'
        desk = self._as(self.agent, ticket)
        with self.assertRaises(UserError):
            desk.action_answer('as_is', '  ')
        with self.assertRaises(UserError):
            desk.action_answer('maybe', 'A2')
        desk.action_answer('as_is', 'Shade A2 on 21', channel='whatsapp')
        self.assertEqual((ticket.state, ticket.outcome, ticket.channel), ('closed', 'as_is', 'whatsapp'))
        self.assertEqual((ticket.closed_by_id, ticket.agent_id), (self.agent, self.agent))
        self.assertTrue(ticket.first_call_answer)
        self.assertEqual(ticket.call_ids.response, 'answered')
        self.assertEqual(ticket.call_ids.channel, 'whatsapp')
        self.assertTrue(order.call_doctor_resolved)
        self.assertFalse(order.call_pending)
        self.assertFalse(order.is_pending_work, "the benches stop being told to ring somebody who has been rung")
        self.assertIn('Handle with care', order.instruction)
        self.assertIn('Shade A2 on 21', order.instruction, "the job card prints the instruction")
        self.assertIn('Shade A2 on 21', ' '.join(order.message_ids.mapped('body')))
        self.assertTrue(self._told(ticket, self.seller), "whoever is waiting for the answer is told it")
        self.assertFalse(self._told(ticket, self.agent), "and whoever wrote it is not told what they wrote")
        with self.assertRaises(UserError):
            desk.action_answer('as_is', 'again')

    def test_an_answer_kept_off_the_job_card_stays_off(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_answer('as_is', 'Price agreed at 900', tell_production=False)
        self.assertNotIn('Price agreed', order.instruction or '')
        self.assertIn('Price agreed', ' '.join(order.message_ids.mapped('body')))

    def test_an_answer_after_missed_calls_is_not_a_first_call_answer(self):
        order, ticket = self._ticket()
        desk = self._as(self.agent, ticket)
        desk.log_call('no_answer')
        desk.action_answer('as_is', 'A2')
        self.assertFalse(ticket.first_call_answer)
        self.assertEqual((ticket.attempts, ticket.unanswered), (2, 1))
        self.assertFalse(ticket.callback_at)

    def test_a_change_or_a_cancellation_is_somebodys_to_do(self):
        for outcome, word in (('change', 'Change'), ('cancel', 'Cancel')):
            with self.subTest(outcome=outcome):
                order, ticket = self._ticket()
                self._as(self.agent, ticket).action_answer(outcome, 'Make it in zirconia instead')
                todo = order.activity_ids.filtered(lambda a: word in (a.summary or ''))
                self.assertEqual(len(todo), 1)
                self.assertEqual(order.state, 'draft', "the desk never changes or cancels an order by itself")
                if outcome == 'cancel':
                    self.assertNotIn('zirconia', order.instruction or '')

    def test_one_call_settles_every_question_for_the_practice(self):
        first_order, first = self._ticket()
        second_order, second = self._ticket(partner=self.clinic, patient='Anu Mathew')
        elsewhere = self.env['res.partner'].create({'name': 'DDK Other Clinic', 'phone': '1'})
        other_order, other = self._ticket(partner=elsewhere)
        self.assertEqual(first.sibling_count, 1)
        self._as(self.agent, first).log_call('no_answer', also=[second.id, other.id])
        self.assertEqual((first.attempts, second.attempts, other.attempts), (1, 1, 0), "only the same practice")
        self._as(self.agent, first).action_answer('as_is', 'Both as written', also=[second.id, other.id])
        self.assertEqual((first.state, second.state, other.state), ('closed', 'closed', 'new'))
        self.assertTrue(second_order.call_doctor_resolved)
        self.assertFalse(other_order.call_doctor_resolved)

    def test_going_ahead_without_the_doctor_is_a_leads_decision(self):
        order, ticket = self._ticket()
        with self.assertRaises(AccessError):
            self._as(self.agent, ticket).action_answer('unreached', 'Three days, no answer')
        for thin in ('', 'no'):
            with self.assertRaises(UserError):
                self._as(self.lead, ticket).action_answer('unreached', thin)
        self._as(self.lead, ticket).action_answer('unreached', 'Three days without an answer: made as written')
        self.assertEqual((ticket.state, ticket.outcome), ('closed', 'unreached'))
        self.assertFalse(ticket.call_ids, "nobody answered, so no answered call is invented")
        self.assertFalse(ticket.first_call_answer)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    # ------------------------------------------------------------------ the order moves on
    def test_an_order_held_for_the_doctor_comes_back_to_the_queue(self):
        self.env['ir.config_parameter'].sudo().set_param('lab_order_control.require_order_verification', 'True')
        order, ticket = self._ticket()
        order.write({'verification_state': 'to_verify'})
        order._put_on_hold(reason='awaiting_doctor_response', note='cannot reach')
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        self.assertEqual(order.verification_state, 'to_verify')
        held, second = self._ticket()
        held.write({'verification_state': 'to_verify'})
        held._put_on_hold(reason='pricing_approval', note='price not agreed')
        self._as(self.agent, second).action_answer('as_is', 'A2')
        self.assertEqual(held.verification_state, 'on_hold', "held for something else, it stays held")

    def test_the_order_confirms_itself_only_when_the_company_says_so(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        self.assertEqual(order.state, 'draft')
        self.company.doctor_ticket_confirm = True
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        self.assertEqual(order.state, 'sale')
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_answer('change', 'Zirconia')
        self.assertEqual(order.state, 'draft', "a change is never confirmed by itself")

    def test_an_order_that_is_owed_a_check_is_not_confirmed_by_itself(self):
        self.company.doctor_ticket_confirm = True
        self.env['ir.config_parameter'].sudo().set_param('lab_order_control.require_order_verification', 'True')
        order, ticket = self._ticket()
        if not order._verification_required():
            order.verification_needed = True
        order.write({'verification_state': 'to_verify'})
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        self.assertEqual(order.state, 'draft')

    def test_with_two_questions_the_order_waits_for_both(self):
        order, first = self._ticket()
        second = self.Ticket.sudo().create({'order_id': order.id, 'reason': 'price_approval', 'question': 'Agree 900'})
        self._as(self.agent, first).action_answer('as_is', 'A2')
        self.assertTrue(order.call_pending)
        with self.assertRaises(UserError):
            order.action_confirm()
        self._as(self.agent, second).action_answer('as_is', 'Agreed')
        self.assertFalse(order.call_pending)
        order.action_confirm()

    # ------------------------------------------------------------------ the field
    def test_what_a_phone_cannot_settle_goes_to_the_executive(self):
        order, ticket = self._ticket()
        ticket.raised_by_id = self.executive
        self._as(self.agent, ticket).action_hand_to_field(note='Reception will not put us through')
        self.assertEqual((ticket.state, ticket.field_user_id), ('field', self.executive))
        self.assertFalse(ticket.callback_at)
        todo = ticket.activity_ids.filtered(lambda a: a.user_id == self.executive)
        self.assertEqual(len(todo), 1)
        with self.assertRaises(AccessError):
            self._as(self.nobody, ticket).action_answer('as_is', 'A2')
        with self.assertRaises(AccessError):
            self._as(self.executive, ticket).action_take()
        self._as(self.executive, ticket).action_answer('as_is', 'He said A2, I was there', channel='phone')
        self.assertEqual((ticket.state, ticket.channel), ('closed', 'person'))
        self.assertEqual(ticket.call_ids.channel, 'person')
        self.assertEqual(ticket.closed_by_id, self.executive)
        self.assertFalse(ticket.activity_ids)
        self.assertTrue(order.call_doctor_resolved)

    def test_an_executive_answers_only_what_is_with_them(self):
        order, ticket = self._ticket()
        ticket.raised_by_id = self.executive
        with self.assertRaises(AccessError):
            self._as(self.executive, ticket).action_answer('as_is', 'A2')
        self._as(self.agent, ticket).action_hand_to_field(user_id=self.seller.id)
        with self.assertRaises(AccessError):
            self._as(self.executive, ticket).action_answer('as_is', 'A2')

    def test_a_ticket_comes_back_from_the_field(self):
        order, ticket = self._ticket()
        ticket.raised_by_id = self.executive
        self._as(self.agent, ticket).action_hand_to_field()
        self._as(self.agent2, ticket).action_back_to_desk()
        self.assertEqual((ticket.state, ticket.agent_id), ('taken', self.agent2))
        self.assertFalse(ticket.activity_ids)

    def test_the_case_slip_says_where_the_question_stands(self):
        """The whole chain, as the executive: a slip marked "call the doctor" becomes an order,
        the order raises the ticket, and the slip shows what the desk does with it."""
        clinic = self.env['res.partner'].create({'name': 'DDK Slip Clinic', 'is_clinic': True, 'phone': '1',
                                                 'partner_latitude': 9.98, 'partner_longitude': 76.29, 'visit_radius_m': 200.0})
        visit = self.env['lab.visit'].create({'partner_id': clinic.id, 'user_id': self.executive.id})
        visit.write({'state': 'open', 'check_in': fields.Datetime.now()})
        case = self.env['lab.case'].with_user(self.executive).create({
            'visit_id': visit.id, 'patient': 'Slip Patient', 'needs_doctor_call': True,
            'doctor_call_note': 'A2 or A3 on 21?',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'ul': 'upper', 'quantity': 1.0})]})
        case.action_submit()
        if not case.sale_order_id:
            case.sudo().action_create_order()
        ticket = case.sudo().sale_order_id.doctor_ticket_ids
        self.assertEqual(len(ticket), 1, "raised by the slip, with nobody pressing anything")
        self.assertEqual((ticket.raised_by_id, ticket.question), (self.executive, 'A2 or A3 on 21?'))
        case.invalidate_recordset()
        self.assertEqual((case.doctor_ticket_state, case.doctor_ticket_mine), ('new', False))
        with self.assertRaises(UserError):
            case.action_answer_doctor_ticket()
        self._as(self.agent, ticket).action_hand_to_field(note='Reception will not put us through')
        case.invalidate_recordset()
        self.assertEqual((case.doctor_ticket_state, case.doctor_ticket_mine), ('field', True))
        self.assertIn('A2 or A3 on 21?', case.doctor_ticket_ask)
        self.assertIn('Reception', case.doctor_ticket_ask)
        action = case.action_answer_doctor_ticket()
        wizard = self.env[action['res_model']].with_user(self.executive).with_context(**action['context']).create(
            {'answer': 'A3', 'outcome': 'as_is'})
        self.assertTrue(wizard.in_person)
        self.assertNotIn('unreached', [o[0] for o in wizard._fields['outcome'].selection])
        wizard.action_save()
        case.invalidate_recordset()
        self.assertEqual((case.doctor_ticket_state, case.doctor_ticket_answer), ('closed', 'A3'))
        self.assertEqual(ticket.channel, 'person')
        self.assertIn('Go ahead as written', case.doctor_ticket_line)

    # ------------------------------------------------------------------ leads
    def test_a_lead_withdraws_and_reopens(self):
        order, ticket = self._ticket()
        with self.assertRaises(AccessError):
            self._as(self.agent, ticket).action_withdraw('raised twice')
        self._as(self.lead, ticket).action_withdraw('raised twice')
        self.assertEqual(ticket.state, 'cancelled')
        self.assertFalse(order.call_pending)
        with self.assertRaises(AccessError):
            self._as(self.agent, ticket).action_reopen()
        self._as(self.lead, ticket).action_reopen()
        self.assertEqual(ticket.state, 'taken')
        self.assertTrue(order.call_pending)
        with self.assertRaises(UserError):
            order.action_confirm()
        self._as(self.lead, ticket).action_answer('as_is', 'A2')
        order.action_confirm()
        with self.assertRaises(UserError):
            self._as(self.lead, ticket).action_reopen()

    # ------------------------------------------------------------------ sharing out
    def test_shared_out_a_ticket_goes_to_whoever_has_fewest(self):
        self.company.doctor_ticket_assign = 'spread'
        self.lead.at_doctor_desk = False
        others = self.env.ref('lab_doctor_desk.group_doctor_desk_agent').sudo().all_user_ids - (self.agent | self.agent2 | self.lead)
        others.write({'at_doctor_desk': False})
        first = self._ticket()[1]
        second = self._ticket()[1]
        third = self._ticket()[1]
        self.assertEqual({first.agent_id, second.agent_id}, {self.agent, self.agent2})
        self.assertEqual(first.state, 'taken')
        self.assertIn(third.agent_id, self.agent | self.agent2)
        self.assertTrue(first.activity_ids.filtered(lambda a: a.user_id == first.agent_id))
        self.agent.at_doctor_desk = False
        self.assertEqual(self._ticket()[1].agent_id, self.agent2, "somebody away from the desk is passed by")
        self.agent2.at_doctor_desk = False
        pooled = self._ticket()[1]
        self.assertEqual((pooled.state, pooled.agent_id.id), ('new', False), "nobody at the desk: it waits in the pool")

    # ------------------------------------------------------------------ who sees what
    def test_everybody_sees_their_own_and_the_desk_sees_all(self):
        order, mine = self._ticket()
        mine.raised_by_id = self.executive
        order2, other = self._ticket()
        other.write({'raised_by_id': self.nobody.id, 'salesperson_id': False})
        mine.salesperson_id = False
        seen = lambda user: self.Ticket.with_user(user).search([('id', 'in', (mine | other).ids)])
        self.assertEqual(seen(self.executive), mine)
        self.assertEqual(seen(self.nobody), other)
        self.assertEqual(seen(self.agent), mine | other)
        self.assertEqual(seen(self.lead), mine | other)
        with self.assertRaises(AccessError):
            mine.with_user(self.executive).write({'question': 'changed by hand'})
        with self.assertRaises(AccessError):
            mine.with_user(self.agent).unlink()

    def test_a_ticket_belongs_to_its_company(self):
        order, ticket = self._ticket()
        elsewhere = self.env['res.company'].create({'name': 'DDK Other Lab'})
        stranger = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Other Desk', 'login': 'ddk_other', 'company_id': elsewhere.id, 'company_ids': [(6, 0, [elsewhere.id])],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('lab_doctor_desk.group_doctor_desk_agent').id])]})
        self.assertFalse(self.Ticket.with_user(stranger).search([('id', '=', ticket.id)]))
        desk = self.env['lab.doctor.desk'].with_user(stranger).with_company(elsewhere).get_desk()
        self.assertNotIn(ticket.id, [c['id'] for c in desk['cards']])
        self.assertFalse(self.env['lab.doctor.desk'].with_user(stranger).with_company(elsewhere).get_ticket(ticket.id))

    # ------------------------------------------------------------------ the desk's screen
    def test_the_board_is_painted_for_the_desk_only(self):
        order, ticket = self._ticket(priority='urgent')
        Desk = self.env['lab.doctor.desk']
        for outsider in (self.nobody, self.seller, self.executive):
            with self.subTest(user=outsider.name), self.assertRaises(AccessError):
                Desk.with_user(outsider).get_desk()
        self._as(self.agent, ticket).action_take()
        board = Desk.with_user(self.agent).get_desk()
        card = next(c for c in board['cards'] if c['id'] == ticket.id)
        self.assertEqual((card['clinic'], card['patient'], card['order']), (self.doctor.display_name, 'Binu Thomas', order.name))
        self.assertTrue(card['mine'])
        self.assertEqual((card['state'], card['priority'], card['due_state']), ('taken', '1', 'ok'))
        self.assertGreater(card['minutes_left'], 0)
        self.assertEqual(board['me'], {'id': self.agent.id, 'name': 'Desk Asha', 'at_desk': True, 'lead': False})
        self.assertGreaterEqual(board['kpis']['mine'], 1)
        self.assertIn(self.company.name, board['on'])
        self.assertNotIn('answered', [r[0] for r in board['responses']])
        self.assertIn(self.agent2.id, [a['id'] for a in board['agents']])
        other = next(c for c in Desk.with_user(self.agent2).get_desk()['cards'] if c['id'] == ticket.id)
        self.assertFalse(other['mine'])

    def test_a_ticket_opens_with_what_is_needed_before_ringing(self):
        order, ticket = self._ticket()
        second_order, second = self._ticket(partner=self.clinic, patient='Anu Mathew')
        desk = self.env['lab.doctor.desk'].with_user(self.agent)
        self._as(self.agent, ticket).log_call('busy', remarks='in surgery')
        view = desk.get_ticket(ticket.id)
        self.assertEqual(view['tel'], 'tel:+919847011111')
        self.assertEqual([l['name'] for l in view['lines']], [self.product.display_name])
        self.assertEqual([o['id'] for o in view['others']], [second.id])
        self.assertEqual([e['tone'] for e in view['events']], ['raised', 'taken', 'missed'])
        self.assertIn('in surgery', view['events'][-1]['note'])
        self.assertFalse(view['can_open_order'], "the desk role alone does not open orders")
        self.assertEqual(view['habits']['calls'], 1)
        if 'whatsapp_number' in self.doctor._fields:
            self.assertTrue(view['wa'].startswith('https://wa.me/919847022222?text='), "the WhatsApp number, never the phone")
            self.assertIn('Binu%20Thomas', view['wa'])
        self.doctor.write({'phone': False})
        self.clinic.write({'phone': False})
        if 'whatsapp_number' in self.doctor._fields:
            self.doctor.whatsapp_number = False
        third_order, third = self._ticket()
        bare = desk.get_ticket(third.id)
        self.assertEqual((bare['tel'], bare['wa']), ('', ''), "no number is no link, never the wrong number")

    def test_closed_today_is_on_the_board(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        board = self.env['lab.doctor.desk'].with_user(self.agent).get_desk()
        self.assertNotIn(ticket.id, [c['id'] for c in board['cards']])
        done = next(c for c in board['done'] if c['id'] == ticket.id)
        self.assertEqual((done['outcome'], done['answer'], done['closed_by']), ('as_is', 'A2', 'Desk Asha'))
        self.assertGreaterEqual(board['kpis']['closed_today'], 1)
        self.assertIsNotNone(board['kpis']['first_call'])

    def test_away_from_the_desk_is_the_users_own_switch(self):
        desk = self.env['lab.doctor.desk']
        self.assertFalse(desk.with_user(self.agent).set_at_desk(False))
        self.assertFalse(self.agent.at_doctor_desk)
        self.assertTrue(desk.with_user(self.agent).set_at_desk(True))
        with self.assertRaises(AccessError):
            desk.with_user(self.nobody).set_at_desk(False)

    # ------------------------------------------------------------------ what is known about the doctor
    def test_when_a_practice_picks_up_is_learnt_from_its_calls(self):
        order, ticket = self._ticket()
        Log = self.env['lab.doctor.call.log'].sudo()
        tz = self.Ticket._desk_tz()
        import pytz
        from datetime import datetime
        def at(hour, response, day):
            local = tz.localize(datetime(2026, 9, day, hour, 20))
            Log.create({'order_id': order.id, 'response': response,
                        'call_datetime': local.astimezone(pytz.utc).replace(tzinfo=None)})
        for day in (1, 2, 3):
            at(10, 'no_answer', day)
            at(13, 'answered', day)
        at(14, 'answered', 4)
        habits = self.Ticket.doctor_habits(self.clinic.id)
        self.assertEqual((habits['calls'], habits['answered'], habits['rate']), (7, 4, 57))
        self.assertIn('1 pm and 3 pm', habits['best'])
        self.assertIn('4 of 4', habits['best'])
        ten = next(h for h in habits['hours'] if h['hour'] == 10)
        self.assertEqual((ten['tried'], ten['answered']), (3, 0))
        unknown = self.Ticket.doctor_habits(self.env['res.partner'].create({'name': 'Never Rung'}).id)
        self.assertEqual((unknown['calls'], unknown['best']), (0, ''))

    # ------------------------------------------------------------------ the watch
    def test_what_is_late_is_escalated_once(self):
        order, ticket = self._ticket()
        self.assertEqual(self.Ticket.search([('overdue', '=', True), ('id', '=', ticket.id)]), self.Ticket)
        ticket.deadline = fields.Datetime.now() - timedelta(minutes=5)
        ticket.invalidate_recordset()
        self.assertTrue(ticket.overdue)
        self.assertEqual(ticket.due_state, 'late')
        self.assertEqual(self.Ticket.search([('overdue', '=', True), ('id', '=', ticket.id)]), ticket)
        self.assertFalse(self.Ticket.search([('overdue', '=', False), ('id', '=', ticket.id)]))
        self.Ticket._cron_watch()
        self.assertTrue(ticket.escalated)
        self.assertEqual(len(self._told(ticket, self.lead)), 1)
        self.Ticket._cron_watch()
        self.assertEqual(len(self._told(ticket, self.lead)), 1)

    def test_a_callback_whose_time_has_come_is_told_once(self):
        order, ticket = self._ticket()
        self._as(self.agent, ticket).log_call('no_answer')
        ticket.callback_at = fields.Datetime.now() - timedelta(minutes=1)
        told = lambda: self._told(ticket, self.agent)
        self.Ticket._cron_watch()
        self.assertEqual(len(told()), 1)
        self.Ticket._cron_watch()
        self.assertEqual(len(told()), 1)
        self._as(self.agent, ticket).action_set_callback(fields.Datetime.to_string(fields.Datetime.now() - timedelta(seconds=30)))
        self.Ticket._cron_watch()
        self.assertEqual(len(told()), 2, "a new time is a new reminder")

    # ------------------------------------------------------------------ the order's own page
    def test_the_order_says_where_its_ticket_stands(self):
        order, ticket = self._ticket()
        self.assertEqual((order.doctor_ticket_count, order.doctor_ticket_open), (1, 1))
        self.assertIn(ticket.name, order.doctor_ticket_note)
        self._as(self.agent, ticket).log_call('no_answer')
        order.invalidate_recordset()
        self.assertIn('1 attempt', order.doctor_ticket_note)
        action = order.action_log_doctor_call()
        self.assertEqual((action['res_model'], action['res_id']), ('lab.doctor.ticket', ticket.id),
                         "with a ticket open, a call is logged on the ticket")
        self._as(self.agent, ticket).action_answer('as_is', 'A2')
        order.invalidate_recordset()
        self.assertEqual(order.doctor_ticket_open, 0)

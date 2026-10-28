# -*- coding: utf-8 -*-
"""Epic B — delivery, delay detection, notifications (R4-R8).

The spec's own acceptance test: a delivery scheduled 10:00 with 120-min grace becomes
delayed at 12:01, exactly one executive warning; at +4h a manager activity exists; a
delivery delivered at 12:30 shows late_delivered; no duplicate notifications on repeated
cron runs.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestEpicBDelivery(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_str('lab_delivery.grace_minutes', '120')
        cls.params.set_str('lab_delivery.emergency_grace_minutes', '30')
        cls.params.set_str('lab_delivery.escalation_after_hours', '4')
        cls.clinic = cls.env['res.partner'].create({'name': 'Epic B Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Epic B Appliance', 'type': 'consu', 'list_price': 200.0})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Delivery Exec', 'login': 'epicb_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})

    def _delivery(self, scheduled, **kw):
        return self.env['lab.delivery'].create(dict({
            'sale_order_id': self._order().id,
            'executive_id': self.exec_user.id,
            'scheduled_date': scheduled,
        }, **kw))

    def _at(self, when):
        return patch('odoo.fields.Datetime.now', return_value=when)

    # ------------------------------------------------------------------ R5
    def test_near_place_without_a_name_is_impossible(self):
        delivery = self._delivery(fields.Datetime.now())
        delivery.state = 'out'
        wiz = self.env['lab.delivery.done.wizard'].create({
            'delivery_id': delivery.id, 'delivery_outcome': 'near_place'})
        with self.assertRaises(UserError):
            wiz.action_confirm()

    def test_marking_delivered_stores_the_outcome(self):
        delivery = self._delivery(fields.Datetime.now())
        delivery.state = 'out'
        wiz = self.env['lab.delivery.done.wizard'].create({
            'delivery_id': delivery.id, 'delivery_outcome': 'near_place',
            'near_place_name': 'Sunrise Pharmacy'})
        wiz.action_confirm()
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.near_place_name, 'Sunrise Pharmacy')

    # ------------------------------------------------------------------ R6
    def test_setting_emergency_manually_needs_a_reason(self):
        delivery = self._delivery(fields.Datetime.now())
        with self.assertRaises(UserError):
            delivery.write({'is_emergency': True})
        delivery.write({'is_emergency': True, 'emergency_reason': 'Chairside breakage'})
        self.assertTrue(delivery.is_emergency)

    def test_an_emergency_order_flags_its_delivery_automatically(self):
        order = self._order()
        order.priority = 'emergency'
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_user.id,
            'scheduled_date': fields.Datetime.now()})
        self.assertTrue(delivery.is_emergency)

    # ------------------------------------------------------------------ R7/R8 — the spec's own scenario
    def test_delay_and_escalation_exactly_once_per_window(self):
        start = datetime(2026, 8, 10, 10, 0, 0)
        delivery = self._delivery(start)
        delivery.state = 'out'
        Delivery = self.env['lab.delivery']

        with self._at(start + timedelta(hours=1, minutes=59)):
            Delivery._cron_delay_check()
        delivery.invalidate_recordset()
        self.assertFalse(delivery.is_delayed, "still inside the 120-minute grace")

        with self._at(start + timedelta(hours=2, minutes=1)):
            Delivery._cron_delay_check()
        delivery.invalidate_recordset()
        self.assertTrue(delivery.is_delayed)
        self.assertTrue(delivery.first_delayed_notified)
        exec_activities = self.env['mail.activity'].search([
            ('res_model', '=', 'lab.delivery'), ('res_id', '=', delivery.id),
            ('user_id', '=', self.exec_user.id)])
        self.assertEqual(len(exec_activities), 1,
                         "exactly one executive warning, not one per cron tick")

        # Repeated cron runs inside the same window must not add a second warning.
        with self._at(start + timedelta(hours=2, minutes=30)):
            Delivery._cron_delay_check()
        exec_activities_2 = self.env['mail.activity'].search([
            ('res_model', '=', 'lab.delivery'), ('res_id', '=', delivery.id),
            ('user_id', '=', self.exec_user.id)])
        self.assertEqual(len(exec_activities_2), 1, "no duplicate on repeated cron runs")

        # +4h from the first warning: escalation, manager notified.
        with self._at(start + timedelta(hours=2) + timedelta(hours=4, minutes=1)):
            Delivery._cron_delay_check()
        delivery.invalidate_recordset()
        self.assertGreaterEqual(delivery.escalation_count, 1)

    def test_the_delay_check_walks_every_live_parcel_a_page_at_a_time(self):
        """It took the same first 400 open rows every run and re-triggered itself for
        ever, never reaching the rest. Each run now moves on, and stops re-triggering
        once there is nothing left to tell. (2026-09-15)"""
        start = datetime(2026, 8, 10, 10, 0, 0)
        parcels = self.env['lab.delivery']
        for _i in range(5):
            parcels |= self._delivery(start)
        parcels.write({'state': 'out'})
        Delivery = self.env['lab.delivery']
        # Pinned to this fixture: a copy of the live database has late parcels of its
        # own, and the paging arithmetic below is about these five.
        live = patch.object(type(Delivery), '_live_ids',
                            lambda self, limit=None: parcels.ids)
        trigger = patch.object(type(self.env['ir.cron']), '_trigger', autospec=True)

        runs = []
        with live, trigger as triggered, self._at(start + timedelta(hours=3)):
            for _run in range(4):
                triggered.reset_mock()
                handled = Delivery._cron_delay_check(batch=2)
                runs.append((handled, triggered.call_count))
        self.assertEqual(runs, [(2, 1), (2, 1), (1, 0), (0, 0)],
                         "two, two, the last one, then silence - never the same page")
        parcels.invalidate_recordset()
        self.assertTrue(all(parcels.mapped('first_delayed_notified')))

    def test_the_delay_check_leaves_shipped_paperwork_alone(self):
        """A draft dispatch whose order already shipped is not late, it is finished -
        and the 400 of those an earlier run flagged are cleared, not re-warned."""
        start = datetime(2026, 8, 10, 10, 0, 0)
        live, ghost = self._delivery(start), self._delivery(start)
        ghost.write({'is_delayed': True, 'delay_hours': 99.0})
        Delivery = self.env['lab.delivery']
        with patch.object(type(Delivery), '_live_ids',
                          lambda self, limit=None: live.ids), \
                self._at(start + timedelta(hours=3)):
            Delivery._cron_delay_check()
        (live | ghost).invalidate_recordset()
        self.assertTrue(live.first_delayed_notified)
        self.assertFalse(ghost.first_delayed_notified, "no warning about a shipped box")
        self.assertFalse(ghost.is_delayed, "and no stale late flag left on it")

    def test_delivered_after_the_deadline_is_marked_late(self):
        start = datetime(2026, 8, 10, 10, 0, 0)
        delivery = self._delivery(start)
        delivery.state = 'out'
        with self._at(start + timedelta(hours=2, minutes=30)):
            wiz = self.env['lab.delivery.done.wizard'].create(
                {'delivery_id': delivery.id, 'delivery_outcome': 'clinic'})
            wiz.action_confirm()
        self.assertTrue(delivery.late_delivered)

    def test_emergency_uses_the_shorter_grace(self):
        start = datetime(2026, 8, 10, 10, 0, 0)
        delivery = self._delivery(
            start, is_emergency=True, emergency_reason='Chairside',
            promised_datetime=start)
        delivery.state = 'out'
        with self._at(start + timedelta(minutes=45)):
            self.env['lab.delivery']._cron_delay_check()
        delivery.invalidate_recordset()
        self.assertTrue(delivery.is_delayed,
                        "45 minutes must already be late against a 30-minute grace")

    # ------------------------------------------------------------------ visit cross-check
    def test_closing_a_visit_as_delivered_with_an_open_delivery_is_blocked(self):
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        order = self._order()
        if 'patient' in order._fields:
            order.patient = 'Rani'
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': fields.Datetime.now(),
            'state': 'assigned'})
        visit.outcome = 'delivered'
        with self.assertRaises(UserError) as caught:
            visit.do_check_out()
        # the case as the executive knows it - order, patient, work - not only DLV/...
        # (client, 2026-10-22)
        message = str(caught.exception)
        for text in (order.name, 'Epic B Appliance ×1', delivery.name, 'Epic B Clinic'):
            self.assertIn(text, message)
        if 'patient' in order._fields:
            self.assertIn('Patient Rani', message)

    # ------------------------------------------------- handed over before the visit
    def _handed_over(self, when):
        """A case scanned and confirmed at the door before any visit was made."""
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': self._order().id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': when, 'state': 'out'})
        delivery.write({'state': 'delivered', 'delivery_outcome': 'clinic',
                        'delivered_datetime': when})
        self.assertFalse(delivery.visit_id, "no visit existed to attach it to")
        return delivery

    def test_a_handover_before_the_visit_is_on_the_visit(self):
        """28 Oct: Delivered at 11:41, the visit made at 11:42, refused at checkout
        over two cases scanned at that door on 22 Oct."""
        self.exec_user.tz = 'Asia/Kolkata'
        handed = self._handed_over(datetime(2026, 10, 28, 6, 11, 58))     # 11:41 IST
        stale = self.env['lab.delivery'].create({
            'sale_order_id': self._order().id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'state': 'assigned',
            'scheduled_date': datetime(2026, 10, 22, 12, 6, 44)})
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'date': fields.Date.to_date('2026-10-28')})
        visit.do_check_in(latitude=9.98, longitude=76.58)
        self.assertEqual(handed.visit_id, visit, "the visit takes the morning's hand-over")
        visit.outcome = 'delivered'
        visit.do_check_out(latitude=9.98, longitude=76.58)
        self.assertEqual(visit.state, 'done', "the outcome is true: the executive goes")
        self.assertEqual(stale.state, 'assigned', "the old case is not closed for them")
        self.assertTrue(any(stale.name in (m.body or '') for m in visit.message_ids),
                        "but the visit says it is still open")

    def test_a_handover_on_another_day_stays_off_the_visit(self):
        self.exec_user.tz = 'Asia/Kolkata'
        yesterday = self._handed_over(datetime(2026, 10, 27, 18, 0, 0))   # 23:30 IST
        tomorrow = self._handed_over(datetime(2026, 10, 28, 18, 45, 0))   # 00:15 IST, 29th
        self.env['lab.delivery'].create({
            'sale_order_id': self._order().id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'state': 'assigned',
            'scheduled_date': fields.Datetime.now()})
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'date': fields.Date.to_date('2026-10-28'),
            'state': 'open', 'check_in': fields.Datetime.now()})
        visit._attach_handovers()
        self.assertFalse((yesterday | tomorrow).visit_id)
        visit.outcome = 'delivered'
        with self.assertRaises(UserError, msg="nothing was handed over on this visit"):
            visit.do_check_out(latitude=9.98, longitude=76.58)

    # ------------------------------------------------- shipped at the counter
    def _shipped_order(self):
        """An order the office ships by validating its transfer."""
        order = self._order()
        order.action_confirm()
        return order

    def _validate(self, order):
        picking = order.picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing')
        picking.move_ids.quantity = 1
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        self.assertEqual(picking.state, 'done')
        return picking

    def test_validating_the_transfer_closes_the_dispatch(self):
        """The office validates at the counter; the dispatch must not stay out."""
        order = self._shipped_order()
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': fields.Datetime.now(),
            'state': 'assigned'})
        picking = self._validate(order)
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.delivered_datetime, picking.date_done)
        self.assertIn(picking.name, delivery.message_ids[:1].body)

    def test_checkout_as_delivered_passes_when_the_order_has_shipped(self):
        """Transfer done, dispatch left assigned: the executive was refused."""
        order = self._shipped_order()
        self._validate(order)
        ghost = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': fields.Datetime.now(),
            'state': 'out'})
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'state': 'open', 'check_in': fields.Datetime.now()})
        visit.outcome = 'delivered'
        visit.do_check_out(latitude=9.98, longitude=76.58)
        self.assertEqual(visit.state, 'done', "the executive is let go")
        self.assertEqual(ghost.state, 'delivered', "the stale dispatch is closed, not blocking")

    def test_checkout_still_blocks_when_the_work_has_not_shipped(self):
        order = self._shipped_order()        # confirmed, transfer still open
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': fields.Datetime.now(),
            'state': 'out'})
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        visit.outcome = 'delivered'
        with self.assertRaises(UserError):
            visit.do_check_out()

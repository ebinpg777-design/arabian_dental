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
        cls.params.set_param('lab_delivery.grace_minutes', '120')
        cls.params.set_param('lab_delivery.emergency_grace_minutes', '30')
        cls.params.set_param('lab_delivery.escalation_after_hours', '4')
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
        self.env['lab.delivery'].create({
            'sale_order_id': self._order().id, 'executive_id': self.exec_user.id,
            'partner_id': self.clinic.id, 'scheduled_date': fields.Datetime.now(),
            'state': 'assigned'})
        visit.outcome = 'delivered'
        with self.assertRaises(UserError):
            visit.do_check_out()

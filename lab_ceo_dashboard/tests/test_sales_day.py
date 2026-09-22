# -*- coding: utf-8 -*-
"""Sales & Cases by day: today as booked, tomorrow as an outlook. (client, 2026-09-17)"""
from datetime import datetime, time, timedelta

import pytz

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalesDay(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pulse = cls.env['lab.mgmt.pulse'].with_context(tz='Asia/Kolkata')
        cls.today = cls.Pulse._today()
        cls.tomorrow = cls.today + timedelta(days=1)
        cls.product = cls.env['product.product'].create(
            {'name': 'Day Appliance', 'list_price': 5000.0})

    def _order(self, clinic, day, hour=11, confirm=True, **vals):
        order = self.env['sale.order'].create(dict({
            'partner_id': clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        }, **vals))
        if confirm:
            order.action_confirm()
        local = pytz.timezone('Asia/Kolkata').localize(datetime.combine(day, time(hour, 15)))
        order.date_order = local.astimezone(pytz.utc).replace(tzinfo=None)
        return order

    def test_today_is_the_default_and_the_rail_ends_on_tomorrow(self):
        data = self.Pulse.get_sales_day()
        self.assertEqual(data['day'], fields.Date.to_string(self.today))
        self.assertEqual(data['mode'], 'booked')
        self.assertTrue(data['is_today'])
        self.assertEqual(len(data['rail']), 15)
        self.assertTrue(data['rail'][-1]['future'])
        self.assertEqual(data['rail'][-1]['day'], fields.Date.to_string(self.tomorrow))
        self.assertTrue(data['rail'][-2]['is_today'])

    def test_a_case_booked_today_is_on_today(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Today'})
        before = self.Pulse.get_sales_day()['booked']['orders']
        order = self._order(clinic, self.today, hour=0)   # 00:15 local is still today
        self._order(clinic, self.today, confirm=False)    # a draft is not a case
        data = self.Pulse.get_sales_day()
        booked = data['booked']
        self.assertEqual(booked['orders'], before + 1)
        self.assertIn(order.id, [r['id'] for r in booked['feed']] if not booked['more'] else [order.id])
        self.assertIn(clinic.id, [f['id'] for f in booked['first_timers']])
        self.assertEqual(data['rail'][-2]['orders'], booked['orders'])

    def test_the_day_edges_are_local_not_utc(self):
        data = self.Pulse.get_sales_day(fields.Date.to_string(self.today))
        start = fields.Datetime.to_datetime(data['bounds'][0][2])
        self.assertEqual(start, datetime.combine(self.today, time.min) - timedelta(hours=5, minutes=30))

    def test_a_past_day_reads_what_was_booked_then(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Past'})
        day = self.today - timedelta(days=3)
        self._order(clinic, day, hour=23)
        booked = self.Pulse.get_sales_day(fields.Date.to_string(day))['booked']
        self.assertIn(clinic.id, [f['id'] for f in booked['first_timers']])
        self.assertEqual(booked['not_yet'], [], "only today can still be acted on")
        self.assertIn(23, [h['hour'] for h in booked['hours'] if h['orders']])

    def test_tomorrow_is_an_outlook_built_from_the_same_weekdays(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Regular'})
        for weeks in (1, 2, 3):
            self._order(clinic, self.tomorrow - timedelta(days=7 * weeks))
        data = self.Pulse.get_sales_day(fields.Date.to_string(self.tomorrow))
        self.assertEqual(data['mode'], 'outlook')
        outlook = data['outlook']
        self.assertEqual(len(outlook['weeks']), 4)
        self.assertEqual(outlook['weeks'][-1]['day'],
                         fields.Date.to_string(self.tomorrow - timedelta(days=7)))
        self.assertTrue(outlook['low'] <= outlook['expected'] <= outlook['high'])
        regular = next(r for r in self.Pulse._regulars(self.tomorrow, limit=10000)
                       if r['id'] == clinic.id)
        self.assertEqual(regular['weeks'], 3)
        self.assertEqual(regular['dots'], [False, True, True, True])
        self.assertGreaterEqual(outlook['regulars_total'], 1)

    def test_a_regular_who_booked_today_is_not_chased(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Chase'})
        for weeks in (1, 2, 3, 4):
            self._order(clinic, self.today - timedelta(days=7 * weeks))
        ids = [r['id'] for r in self.Pulse._regulars(self.today, limit=10000)]
        self.assertIn(clinic.id, ids)
        booked = self._order(clinic, self.today)
        ids = [r['id'] for r in self.Pulse._regulars(
            self.today, exclude=booked.partner_id, limit=10000)]
        self.assertNotIn(clinic.id, ids)

    def test_follow_ups_due_tomorrow_are_counted(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Held'})
        order = self._order(clinic, self.today)
        before = self.Pulse._followups_due(self.tomorrow)['count']
        order.write({'hold_reason': 'missing_info', 'next_followup_date': self.tomorrow})
        self.assertEqual(self.Pulse._followups_due(self.tomorrow)['count'], before + 1)

    def test_a_salesperson_narrows_the_day(self):
        clinic = self.env['res.partner'].create({'name': 'Day Clinic Person'})
        person = self.env['res.users'].create({
            'name': 'Day Salesperson', 'login': 'day_salesperson',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])]})
        self._order(clinic, self.today, user_id=person.id)
        booked = self.Pulse.get_sales_day(False, person.id)['booked']
        self.assertEqual(booked['orders'], 1)

    def test_only_management_reads_it(self):
        user = self.env['res.users'].create({
            'name': 'Day Outsider', 'login': 'day_outsider',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env['lab.mgmt.pulse'].with_user(user).get_sales_day()

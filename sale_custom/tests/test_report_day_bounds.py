# -*- coding: utf-8 -*-
"""The Order List and Order Count reports take the reader's whole days.

`date_order` is a datetime kept in UTC. Both reports built their day bounds from the
picked dates as if those were UTC days, so here an order registered between midnight
and 05:30 landed on the previous day's list. (2026-09-15)
"""
from datetime import date, datetime

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReportDayBounds(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Pinned to this lab's normal case - nobody has a timezone set - so the
        # answer does not depend on the machine or the database copy.
        cls.env.user.tz = False
        cls.env.company.partner_id.tz = False
        cls.clinic = cls.env['res.partner'].create({'name': 'Day Bounds Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Day Bounds Appliance', 'type': 'consu', 'list_price': 100.0})

    def _list(self, day):
        return self.env['sale.order.list.report'].create(
            {'date_from': day, 'date_to': day, 'group_by': 'none'})

    def test_the_order_list_runs_from_local_midnight_to_local_midnight(self):
        domain = self._list(date(2026, 9, 1))._domain()
        self.assertIn(('date_order', '>=', datetime(2026, 8, 31, 18, 30)), domain)
        self.assertIn(('date_order', '<', datetime(2026, 9, 1, 18, 30)), domain)

    def test_an_order_taken_after_midnight_is_on_that_day_s_list(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            # 01:30 on 1 September in Kochi.
            'date_order': datetime(2026, 8, 31, 20, 0),
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        self.assertIn(order, self._list(date(2026, 9, 1))._orders())
        self.assertNotIn(order, self._list(date(2026, 8, 31))._orders())

    def test_the_order_count_uses_the_same_days(self):
        wizard = self.env['sale.order.count.report'].create(
            {'date_from': date(2026, 9, 1), 'date_to': date(2026, 9, 30)})
        domain = wizard._order_domain()
        self.assertIn(('date_order', '>=', datetime(2026, 8, 31, 18, 30)), domain)
        self.assertIn(('date_order', '<', datetime(2026, 9, 30, 18, 30)), domain)

    def test_a_reader_s_own_timezone_wins(self):
        self.env.user.tz = 'Asia/Dubai'
        domain = self._list(date(2026, 9, 1))._domain()
        self.assertIn(('date_order', '>=', datetime(2026, 8, 31, 20, 0)), domain)

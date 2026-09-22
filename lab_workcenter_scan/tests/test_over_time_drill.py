# -*- coding: utf-8 -*-
"""Seeing WHICH steps ran over time, not just how many. (client, 2026-09-19)

The people panel on the flow board printed "3 over time" beside a technician
and then gave no way of opening them, so the number was only ever an
accusation - a supervisor could see that something ran long but not what, and
had nothing to take to the person it named.

The number and the list are computed in two different places (a FILTER in the
panel's SQL, a search in the drill), so the thing worth pinning is that they
cannot drift apart.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOverTimeDrill(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tech = cls.env['res.users'].create({
            'name': 'Slow Hand', 'login': 'ot_tech',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.other = cls.env['res.users'].create({
            'name': 'Other Hand', 'login': 'ot_other',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Over Time Bench', 'users': [(6, 0, cls.tech.ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Over Time Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend', 'workcenter_id': cls.bench.id})]})
        cls.Flow = cls.env['lab.flow']
        cls.today = cls.env['lab.station']._lab_today()

    def _finished(self, expected, actual, user=None):
        """A step finished today by `user`, expected `expected` min, took `actual`."""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        now = fields.Datetime.now()
        wo.write({'workcenter_id': self.bench.id,
                  'bench_user_id': (user or self.tech).id,
                  'bench_assigned_at': now, 'accepted_at': now,
                  'handed_over_at': now,
                  'duration_expected': expected, 'duration': actual})
        return wo

    def _ids(self):
        start, end = self.Flow._day_window(self.today)
        return set(self.Flow._over_time_ids(self.tech.id, start, end))

    def test_only_the_steps_that_ran_long_are_listed(self):
        over = self._finished(30, 45)
        self._finished(30, 20)                      # comfortably inside
        self._finished(30, 30)                      # exactly on it is not over
        self.assertEqual(self._ids(), {over.id})

    def test_a_step_with_no_expectation_is_not_judged(self):
        """duration_expected 0 means nobody said how long it should take, which
        is not the same as it having taken too long."""
        self._finished(0, 500)
        self.assertFalse(self._ids())

    def test_somebody_elses_slow_step_is_not_yours(self):
        mine = self._finished(10, 99)
        self._finished(10, 99, user=self.other)
        self.assertEqual(self._ids(), {mine.id})

    def test_the_list_and_the_number_on_the_panel_cannot_disagree(self):
        """The invariant. The panel counts with a FILTER in SQL and the drill
        searches; if they ever part company the number stops being worth
        printing."""
        for expected, actual in ((30, 45), (30, 45), (20, 21), (30, 10), (0, 900)):
            self._finished(expected, actual)
        start, end = self.Flow._day_window(self.today)
        people = self.Flow._people(start, end, [], day=self.today)
        row = next(p for p in people if p['id'] == self.tech.id)
        self.assertEqual(row['over'], 3, "two at 45 and one at 21")
        self.assertEqual(len(self._ids()), row['over'],
                         "the drill must open exactly what the row counted")

    def test_the_drill_opens_those_and_nothing_else(self):
        over = self._finished(30, 45)
        self._finished(30, 5)
        action = self.Flow.action_open_person(
            self.tech.id, self.today.isoformat(), over_only=True)
        self.assertEqual(action['res_model'], 'mrp.workorder')
        self.assertIn('over time', action['name'])
        self.assertEqual(
            set(self.env['mrp.workorder'].search(action['domain']).ids), {over.id})

    def test_the_list_shows_what_it_was_measured_against(self):
        """"55:00" says nothing until the 30:00 it was meant to be is beside it."""
        self._finished(30, 45)
        action = self.Flow.action_open_person(
            self.tech.id, self.today.isoformat(), over_only=True)
        view = self.env['ir.ui.view'].browse(action['views'][0][0])
        self.assertEqual(view, self.env.ref(
            'lab_workcenter_scan.view_workorder_list_over_time'))
        arch = self.env['mrp.workorder'].get_view(view.id, 'list')['arch']
        for field in ('duration_expected', 'duration'):
            self.assertRegex(
                arch, r'name="%s"[^>]*optional="show"' % field,
                "%s has to be on screen in this list" % field)

    def test_without_the_flag_the_whole_day_still_opens(self):
        """The row itself must keep doing what it always did."""
        over, quick = self._finished(30, 45), self._finished(30, 5)
        action = self.Flow.action_open_person(self.tech.id, self.today.isoformat())
        found = set(self.env['mrp.workorder'].search(action['domain']).ids)
        self.assertLessEqual({over.id, quick.id}, found)
        self.assertNotIn('over time', action['name'])

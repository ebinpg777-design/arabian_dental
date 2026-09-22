# -*- coding: utf-8 -*-
"""Targets: what a day was meant to be, and what it was."""
from datetime import datetime, time, timedelta

import pytz

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestWorkTarget(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tech = cls.env['res.users'].create({
            'name': 'Target Tech', 'login': 'tgt_tech',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.lead = cls.env['res.users'].create({
            'name': 'Target Lead', 'login': 'tgt_lead',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Target Bench',
            'users': [(6, 0, cls.tech.ids)],
            'head_user_ids': [(6, 0, cls.lead.ids)]})
        cls.other = cls.env['mrp.workcenter'].create({'name': 'Other Bench'})
        cls.product = cls.env['product.product'].create({
            'name': 'Target Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend', 'workcenter_id': cls.bench.id})]})
        cls.Target = cls.env['lab.work.target']
        cls.today = cls.env['lab.station']._lab_today()

    def _job_done_today(self, user=None, workcenter=None):
        """A work order stamped as taken today by `user` at `workcenter`."""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        wo.write({'workcenter_id': (workcenter or self.bench).id,
                  'bench_user_id': (user or self.tech).id,
                  'bench_assigned_at': fields.Datetime.now()})
        return wo

    # ------------------------------------------------------------------ counting
    def test_done_counts_the_same_work_the_board_counts(self):
        """A target and the chip beside it must never disagree about a day's work."""
        self._job_done_today()
        self._job_done_today()
        row = self.Target.create({
            'date': self.today, 'user_id': self.tech.id,
            'workcenter_id': self.bench.id, 'target': 3})
        row.action_refresh()
        self.assertEqual(row.done_count, 2)
        board = self.env['lab.station']._day_counts(
            self.bench, self.tech, self.today)
        self.assertEqual(board.get(self.tech.id), row.done_count,
                         "the report and the board must count identically")

    def test_achieved_and_met(self):
        self._job_done_today()
        self._job_done_today()
        row = self.Target.create({
            'date': self.today, 'user_id': self.tech.id,
            'workcenter_id': self.bench.id, 'target': 4})
        row.action_refresh()
        self.assertEqual(row.done_count, 2)
        self.assertAlmostEqual(row.achieved_pct, 0.5)   # a fraction: the view's percentage widget multiplies by 100
        self.assertFalse(row.met)

        self._job_done_today()
        self._job_done_today()
        row.action_refresh()
        self.assertEqual(row.done_count, 4)
        self.assertAlmostEqual(row.achieved_pct, 1.0)
        self.assertTrue(row.met, "reaching the target counts as met")

    def test_a_zero_target_never_divides(self):
        """A row set to zero must not blow up the report with a division."""
        row = self.Target.create({
            'date': self.today, 'user_id': self.tech.id, 'target': 0})
        row.action_refresh()
        self.assertEqual(row.achieved_pct, 0.0)
        self.assertFalse(row.met, "nothing to meet is not met")

    def test_a_bench_target_counts_only_that_bench(self):
        self._job_done_today(workcenter=self.bench)
        self._job_done_today(workcenter=self.other)
        row = self.Target.create({
            'date': self.today, 'user_id': self.tech.id,
            'workcenter_id': self.bench.id, 'target': 5})
        row.action_refresh()
        self.assertEqual(row.done_count, 1, "the other bench is not this bench")

    def test_a_whole_day_target_counts_every_bench(self):
        self._job_done_today(workcenter=self.bench)
        self._job_done_today(workcenter=self.other)
        row = self.Target.create({
            'date': self.today, 'user_id': self.tech.id, 'target': 5})
        row.action_refresh()
        self.assertEqual(row.done_count, 2,
                         "a day target follows the person, not the bench")

    # ------------------------------------------------------------------ lookup
    def test_the_bench_target_wins_over_the_day_target(self):
        """Both set: the board is showing a bench, so the bench's number applies."""
        self.Target.create({'date': self.today, 'user_id': self.tech.id, 'target': 20})
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 12})
        found = self.Target.targets_for(self.today, self.tech.ids, self.bench)
        self.assertEqual(found[self.tech.id], 12)

    def test_the_day_target_is_used_when_the_bench_has_none(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id, 'target': 20})
        found = self.Target.targets_for(self.today, self.tech.ids, self.bench)
        self.assertEqual(found[self.tech.id], 20,
                         "a lab that sets one number a day must still see it")

    def test_no_target_at_all_is_simply_absent(self):
        found = self.Target.targets_for(self.today, self.tech.ids, self.bench)
        self.assertNotIn(self.tech.id, found)

    # ------------------------------------------------------------------ board
    def test_the_board_carries_the_target(self):
        self._job_done_today()
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 2})
        rows = self.env['lab.station']._bench_load(self.bench)
        mine = [r for r in rows if r['id'] == self.tech.id]
        self.assertTrue(mine)
        self.assertEqual(mine[0]['day_count'], 1)
        self.assertEqual(mine[0]['target'], 2)
        self.assertFalse(mine[0]['target_met'])

    def test_a_bench_with_no_targets_still_works(self):
        """Off by default: a lab that does not set targets sees what it always saw."""
        self._job_done_today()
        rows = self.env['lab.station']._bench_load(self.bench)
        mine = [r for r in rows if r['id'] == self.tech.id][0]
        self.assertEqual(mine['day_count'], 1)
        self.assertEqual(mine['target'], 0)
        self.assertFalse(mine['target_met'])

    # ------------------------------------------------------------------ rules
    def test_one_target_per_person_per_day_per_bench(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 5})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.Target.create({'date': self.today, 'user_id': self.tech.id,
                                    'workcenter_id': self.bench.id, 'target': 9})
                # The INSERT is deferred, so without this the savepoint closes
                # before the database ever sees the row and the test passes
                # against a table with no index on it at all.
                self.env.flush_all()

    def test_one_whole_day_target_per_person(self):
        """The null case a plain UNIQUE would have let through.

        Two whole-day targets for the same person on the same day are two
        different numbers with nothing to choose between them.
        """
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 5})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.Target.create({'date': self.today, 'user_id': self.tech.id,
                                    'target': 9})
                self.env.flush_all()

    def test_a_bench_target_and_a_day_target_can_coexist(self):
        """The indexes must not stop the pair the board is built to prefer between."""
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 20})
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 12})
        self.env.flush_all()
        self.assertEqual(self.Target.search_count([
            ('date', '=', self.today), ('user_id', '=', self.tech.id)]), 2)

    def test_a_negative_target_is_refused(self):
        with self.assertRaises(ValidationError):
            self.Target.create({'date': self.today, 'user_id': self.tech.id,
                                'target': -1})

    # ------------------------------------------------------------------ wizard
    def test_the_wizard_fills_itself_from_the_bench(self):
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today})
        wizard._onchange_people()
        listed = wizard.line_ids.mapped('user_id')
        self.assertIn(self.tech, listed)
        self.assertIn(self.lead, listed, "a lead works the bench too")

    def test_the_wizard_opens_on_the_last_target_each_person_had(self):
        """The screen is opened to set TOMORROW, which has no targets yet by
        definition - so looking only at the day being set meant every line
        opened at zero and the manager retyped yesterday's figures.
        (client, 2026-09-12)"""
        yesterday = fields.Date.subtract(self.today, days=1)
        self.Target.create({'date': yesterday, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 9})
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today})
        wizard._onchange_people()
        line = wizard.line_ids.filtered(lambda l: l.user_id == self.tech)
        self.assertEqual(line.target, 9, "yesterday's number, ready to keep or change")

    def test_a_target_already_set_for_the_day_wins_over_an_older_one(self):
        self.Target.create({'date': fields.Date.subtract(self.today, days=2),
                            'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 4})
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 11})
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today})
        wizard._onchange_people()
        line = wizard.line_ids.filtered(lambda l: l.user_id == self.tech)
        self.assertEqual(line.target, 11, "the day's own number, not the older one")

    def test_a_bench_target_is_not_offered_as_a_whole_day_default(self):
        """The two are different promises, so one must not seed the other."""
        self.Target.create({'date': fields.Date.subtract(self.today, days=1),
                            'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 9})
        wizard = self.env['lab.work.target.wizard'].create({
            'date_from': self.today, 'date_to': self.today})      # no bench
        wizard._onchange_people()
        line = wizard.line_ids.filtered(lambda l: l.user_id == self.tech)
        self.assertEqual(line.target, 0, "a bench number is not a whole-day number")

    def test_a_number_nobody_has_revisited_in_a_quarter_is_not_offered(self):
        self.Target.create({'date': fields.Date.subtract(self.today, days=120),
                            'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 9})
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today})
        wizard._onchange_people()
        line = wizard.line_ids.filtered(lambda l: l.user_id == self.tech)
        self.assertEqual(line.target, 0, "too old to read as this week's intention")

    def test_the_wizard_sets_a_whole_week(self):
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today,
            'date_to': fields.Date.add(self.today, days=6),
            'skip_weekends': False,
            'line_ids': [(0, 0, {'user_id': self.tech.id, 'target': 7})]})
        wizard.action_save()
        rows = self.Target.search([('user_id', '=', self.tech.id),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual(len(rows), 7, "one row per day of the range")
        self.assertEqual(set(rows.mapped('target')), {7})

    def test_the_wizard_skips_sundays(self):
        start = self.today
        while start.weekday() != 0:      # start on a Monday so the range holds one
            start = fields.Date.add(start, days=1)
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': start, 'date_to': fields.Date.add(start, days=6),
            'skip_weekends': True,
            'line_ids': [(0, 0, {'user_id': self.tech.id, 'target': 7})]})
        wizard.action_save()
        rows = self.Target.search([('user_id', '=', self.tech.id),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual(len(rows), 6, "Sunday gets no target")
        self.assertNotIn(6, [d.weekday() for d in rows.mapped('date')])

    def test_the_wizard_updates_rather_than_duplicates(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 3})
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today,
            'line_ids': [(0, 0, {'user_id': self.tech.id, 'target': 11})]})
        wizard.action_save()
        rows = self.Target.search([('date', '=', self.today),
                                   ('user_id', '=', self.tech.id),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual(len(rows), 1, "the same day must not gain a second row")
        self.assertEqual(rows.target, 11)

    def test_a_refresher_with_no_timezone_counts_the_labs_day(self):
        """129 of 156 users - and the hourly cron's - have no timezone, and the day
        was counted in UTC for them, so a stored count flipped with whoever
        refreshed last. The lab's day is the company's zone, else Kolkata.
        (review, 2026-09-15)"""
        notz = self.env['res.users'].create({
            'name': 'No TZ Refresher', 'login': 'tgt_notz', 'tz': False,
            'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        Station = self.env['lab.station'].with_user(notz).with_context(tz=False)
        tz = Station._lab_tz()
        self.assertEqual(tz.zone, notz.company_id.partner_id.tz or 'Asia/Kolkata')
        today = Station._lab_today()
        wo = self._job_done_today()
        # Half past midnight in the lab: still yesterday in UTC, anywhere east of it.
        wo.bench_assigned_at = tz.localize(datetime.combine(today, time(0, 30))) \
            .astimezone(pytz.utc).replace(tzinfo=None)
        row = self.Target.create({'date': today, 'user_id': self.tech.id,
                                  'workcenter_id': self.bench.id, 'target': 1})
        row.with_user(notz).sudo().with_context(tz=False).action_refresh()
        self.assertEqual(row.done_count, 1, "the job belongs to the lab's day")
        self.assertTrue(row.met)

    def test_a_target_is_named_by_person_day_and_bench(self):
        """`name_get` is never called in Odoo 19, so every breadcrumb showed only the
        technician. (review, 2026-09-15)"""
        row = self.Target.create({'date': self.today, 'user_id': self.tech.id,
                                  'workcenter_id': self.bench.id, 'target': 1})
        self.assertEqual(row.display_name, '%s · %s · %s' % (
            self.tech.name, self.today, self.bench.display_name))
        whole = self.Target.create({'date': self.today, 'user_id': self.lead.id,
                                    'target': 1})
        self.assertTrue(whole.display_name.endswith('any bench'))

    def test_the_cron_recounts_without_anybody_pressing_anything(self):
        row = self.Target.create({'date': self.today, 'user_id': self.tech.id,
                                  'workcenter_id': self.bench.id, 'target': 2})
        self.assertEqual(row.done_count, 0)
        self._job_done_today()
        self.Target._cron_refresh()
        row.invalidate_recordset()
        self.assertEqual(row.done_count, 1)
    # ------------------------------------------------------------- the wizard's form
    def test_the_wizard_saves_through_the_real_form(self):
        """The bug the floor hit: "Missing required value for the field 'Technician'".

        The lines are built by the onchange, so every one is NEW when the wizard is
        saved - and Odoo does not send a readonly field back to the server, so each
        line arrived without the person it was about and the whole save failed.
        Driven through `Form`, which replays the same onchange and readonly
        behaviour the browser does. (client, 2026-09-10)
        """
        from odoo.tests import Form
        wizard_form = Form(self.env['lab.work.target.wizard'])
        wizard_form.workcenter_id = self.bench
        wizard_form.date_from = self.today
        wizard_form.date_to = self.today
        self.assertTrue(wizard_form.line_ids, "the bench's people must be listed")
        with wizard_form.line_ids.edit(0) as line:
            line.target = 9
        wizard = wizard_form.save()

        self.assertTrue(all(l.user_id for l in wizard.line_ids),
                        "every line must still know whose target it is")
        wizard.action_save()
        rows = self.Target.search([('date', '=', self.today),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertTrue(rows, "the targets must actually be written")
        self.assertIn(9, rows.mapped('target'))

    def test_a_line_with_no_technician_is_ignored_not_fatal(self):
        """One blank row must not cost the manager the whole screen."""
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today,
            'line_ids': [(0, 0, {'user_id': self.tech.id, 'target': 4}),
                         (0, 0, {'target': 7})]})
        wizard.action_save()
        rows = self.Target.search([('date', '=', self.today),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual(len(rows), 1, "only the real technician gets a target")
        self.assertEqual(rows.user_id, self.tech)
        self.assertEqual(rows.target, 4)

    def test_no_technicians_at_all_says_so(self):
        """An empty wizard explains itself rather than writing nothing in silence."""
        wizard = self.env['lab.work.target.wizard'].create({
            'workcenter_id': self.bench.id,
            'date_from': self.today, 'date_to': self.today,
            'line_ids': [(0, 0, {'target': 7})]})
        with self.assertRaises(UserError):
            wizard.action_save()




@tagged('post_install', '-at_install')
class TestTargetReport(TransactionCase):
    """The Against-target half of Production Reports, and the bench line on the
    station board. Scoped to its own people: this suite runs on a database
    where other technicians have stamps today, so nothing here asserts a
    lab-wide total."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tech = cls.env['res.users'].create({
            'name': 'Report Tech', 'login': 'rpt_tech',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.free = cls.env['res.users'].create({
            'name': 'Report Free', 'login': 'rpt_free',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.lead = cls.env['res.users'].create({
            'name': 'Report Lead', 'login': 'rpt_lead',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Report Bench',
            'users': [(6, 0, (cls.tech | cls.free).ids)],
            'head_user_ids': [(6, 0, cls.lead.ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Report Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend', 'workcenter_id': cls.bench.id})]})
        cls.Report = cls.env['lab.mrp.report']
        cls.Target = cls.env['lab.work.target']
        cls.today = cls.env['lab.station']._lab_today()
        # A plain function, not a class attribute: assigned there it would be
        # bound and passed the test as its date.
        cls.iso = staticmethod(fields.Date.to_string)

    def _job_today(self, user):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        wo.write({'bench_user_id': user.id, 'bench_assigned_at': fields.Datetime.now()})
        return wo

    def _person(self, data, user):
        return next((p for p in data['people'] if p['id'] == user.id), None)

    # ------------------------------------------------------------ the report
    def test_default_period_is_this_week(self):
        data = self.Report.get_targets()
        self.assertEqual(len(data['days']), 7)
        self.assertEqual(fields.Date.to_date(data['from']).weekday(), 0, "starts on Monday")
        self.assertTrue(any(d['is_today'] for d in data['days']))
        self.assertEqual([d['weekend'] for d in data['days']][-2:], [True, True])

    def test_a_short_day_and_a_met_day_read_as_such(self):
        self._job_today(self.tech)
        self._job_today(self.tech)
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 3})
        data = self.Report.get_targets(self.iso(self.today), self.iso(self.today))
        row = self._person(data, self.tech)
        self.assertTrue(row, "a person with a target is on the report")
        cell = row['days'][0]
        self.assertEqual((cell['done'], cell['target'], cell['state'], cell['pct']),
                         (2, 3, 'short', 67))
        self.assertEqual((row['done'], row['target'], row['pct']), (2, 3, 67))
        self.assertEqual((row['days_set'], row['days_met'], row['streak']), (1, 0, 0))

        self._job_today(self.tech)
        data = self.Report.get_targets(self.iso(self.today), self.iso(self.today))
        row = self._person(data, self.tech)
        self.assertEqual(row['days'][0]['state'], 'met')
        self.assertEqual((row['days_met'], row['streak'], row['best']), (1, 1, 3))

    def test_work_with_no_target_is_shown_but_not_judged(self):
        self._job_today(self.free)
        data = self.Report.get_targets(self.iso(self.today), self.iso(self.today))
        row = self._person(data, self.free)
        self.assertTrue(row, "a person with stamps but no target still appears")
        self.assertEqual(row['days'][0]['state'], 'free')
        self.assertIsNone(row['pct'], "no target, no percentage")
        self.assertEqual(row['done'], 1)

    def test_tomorrow_is_future_not_short(self):
        tomorrow = self.today + timedelta(days=1)
        self.Target.create({'date': tomorrow, 'user_id': self.tech.id, 'target': 5})
        data = self.Report.get_targets(self.iso(self.today), self.iso(tomorrow))
        row = self._person(data, self.tech)
        self.assertEqual([c['state'] for c in row['days']], ['none', 'future'])
        self.assertEqual(row['target'], 0, "a day that has not come counts for nothing yet")

    def test_whole_day_target_beats_the_sum_of_benches(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 3})
        self.Target.create({'date': self.today, 'user_id': self.tech.id, 'target': 10})
        data = self.Report.get_targets(self.iso(self.today), self.iso(self.today))
        self.assertEqual(self._person(data, self.tech)['days'][0]['target'], 10)

    def test_bench_totals_count_only_work_done_there(self):
        self._job_today(self.tech)
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 4})
        data = self.Report.get_targets(self.iso(self.today), self.iso(self.today))
        bench = next(s for s in data['stations'] if s['id'] == self.bench.id)
        self.assertEqual((bench['target'], bench['done'], bench['people'], bench['pct']),
                         (4, 1, 1, 25))

    def test_the_period_is_capped_and_ordered(self):
        late = self.today + timedelta(days=200)
        data = self.Report.get_targets(self.iso(late), self.iso(self.today))
        self.assertEqual(data['from'], self.iso(self.today), "reversed dates are swapped")
        self.assertEqual(len(data['days']), 62, "two months is a screen")

    # ------------------------------------------------------------ the drills
    def test_a_persons_cases_open_by_case(self):
        wo = self._job_today(self.tech)
        action = self.Report.open_person_cases(
            self.tech.id, self.iso(self.today), self.iso(self.today))
        self.assertEqual(action['res_model'], 'mrp.workorder')
        found = self.env['mrp.workorder'].search(action['domain'])
        self.assertIn(wo, found)
        view = self.env.ref('lab_workcenter_scan.view_workorder_list_case')
        self.assertEqual(action['views'][0], (view.id, 'list'),
                         "the list is the case-shaped one: order, doctor, patient")

    def test_the_weekly_drill_is_case_shaped_too(self):
        action = self.Report.open_operations(user_id=self.tech.id)
        self.assertEqual(action['res_model'], 'mrp.workorder')
        view = self.env.ref('lab_workcenter_scan.view_workorder_list_case')
        self.assertEqual(action['views'][0][0], view.id)
        arch = view.arch_db
        for field in ('sale_order_id', 'order_date', 'clinic_id', 'patient'):
            self.assertIn(field, arch)

    def test_the_two_stale_pivots_are_gone(self):
        for xmlid in ('lab_workcenter_scan.menu_production_by_person',
                      'lab_workcenter_scan.menu_production_waiting',
                      'lab_workcenter_scan.action_production_by_person',
                      'lab_workcenter_scan.action_production_waiting'):
            self.assertFalse(self.env.ref(xmlid, raise_if_not_found=False), xmlid)
        menu = self.env.ref('lab_workcenter_scan.menu_mrp_report')
        self.assertEqual(menu.name, 'Production Reports')

    # ------------------------------------------------------------ the station board
    def test_the_bench_line_adds_up_the_chips(self):
        self._job_today(self.tech)
        self._job_today(self.free)
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 4})
        board = self.env['lab.station'].with_user(self.lead).get_station(self.bench.id)
        line = board['bench_day']
        self.assertEqual((line['done'], line['done_against'], line['target'], line['pct']),
                         (2, 1, 4, 25))
        # Three posted here: two technicians and the lead, who counts as a
        # person on the bench even with nothing done.
        self.assertEqual((line['people'], line['with_target'], line['met']), (3, 1, 0))
        self.assertTrue(line['shift'] is None or 0 <= line['shift'] <= 100)
        if line['shift'] is not None:
            self.assertEqual(line['expected'], round(4 * line['shift'] / 100.0))
            self.assertEqual(line['ahead'], 1 - line['expected'])
        else:
            self.assertIsNone(line['ahead'], "nothing is expected of a day nobody works")

    def test_the_bench_line_follows_the_day_being_looked_at(self):
        yesterday = self.today - timedelta(days=1)
        self.Target.create({'date': yesterday, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 6})
        answer = self.env['lab.station'].with_user(self.lead).done_on(
            self.bench.id, self.iso(yesterday))
        line = answer['bench_day']
        self.assertEqual((line['target'], line['done_against']), (6, 0))
        # A past working day is over: nothing more can be expected of it.
        self.assertIn(line['shift'], (None, 100))

    def test_a_past_day_is_over_and_a_sunday_is_nothing(self):
        Station = self.env['lab.station']
        monday = self.today - timedelta(days=self.today.weekday() + 7)
        self.assertEqual(Station._shift_progress(self.bench, monday), 1.0)
        sunday = monday + timedelta(days=6)
        self.assertIsNone(Station._shift_progress(self.bench, sunday))
        ahead = self.today + timedelta(days=8)
        ahead -= timedelta(days=ahead.weekday())      # a Monday to come
        self.assertEqual(Station._shift_progress(self.bench, ahead), 0.0)


@tagged('post_install', '-at_install')
class TestTargetsOnTheBoards(TransactionCase):
    """Targets where the day is read: the flow board's people table and chip,
    and the station board's set-targets dialog."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.tech = cls.env['res.users'].create({
            'name': 'Board Tech', 'login': 'brd_tech', 'group_ids': [(4, mrp_user)]})
        cls.idle = cls.env['res.users'].create({
            'name': 'Board Idle', 'login': 'brd_idle', 'group_ids': [(4, mrp_user)]})
        cls.lead = cls.env['res.users'].create({
            'name': 'Board Lead', 'login': 'brd_lead', 'group_ids': [(4, mrp_user)]})
        cls.boss = cls.env['res.users'].create({
            'name': 'Board Boss', 'login': 'brd_boss',
            'group_ids': [(4, mrp_user),
                          (4, cls.env.ref('lab_workcenter_scan.group_production_manager').id)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Board Bench',
            'users': [(6, 0, (cls.tech | cls.idle).ids)],
            'head_user_ids': [(6, 0, (cls.lead | cls.boss).ids)]})
        cls.other = cls.env['mrp.workcenter'].create({'name': 'Board Other'})
        cls.product = cls.env['product.product'].create({
            'name': 'Board Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend', 'workcenter_id': cls.bench.id})]})
        cls.Target = cls.env['lab.work.target']
        cls.today = cls.env['lab.station']._lab_today()

    def _job_today(self, user, workcenter=None):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        wo.write({'workcenter_id': (workcenter or self.bench).id,
                  'bench_user_id': user.id,
                  'bench_assigned_at': fields.Datetime.now()})
        return wo

    def _row(self, flow, user):
        return next((p for p in flow['people'] if p['id'] == user.id), None)

    # ------------------------------------------------------------ day_targets
    def test_day_targets_reads_the_whole_day_else_the_benches_summed(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 3})
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.other.id, 'target': 2})
        self.Target.create({'date': self.today, 'user_id': self.idle.id, 'target': 7})
        self.Target.create({'date': self.today, 'user_id': self.idle.id,
                            'workcenter_id': self.bench.id, 'target': 1})
        got = self.Target.day_targets(self.today, (self.tech | self.idle).ids)
        self.assertEqual(got, {self.tech.id: 5, self.idle.id: 7})
        self.assertEqual(self.Target.day_targets(self.today, []), {})

    # ------------------------------------------------------------ flow board
    def test_the_flow_board_judges_each_person_against_their_target(self):
        self._job_today(self.tech)
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.bench.id, 'target': 2})
        flow = self.env['lab.flow'].get_flow()
        row = self._row(flow, self.tech)
        self.assertEqual((row['target'], row['done'], row['target_met'], row['target_pct']),
                         (2, 1, False, 50))
        self._job_today(self.tech)
        row = self._row(self.env['lab.flow'].get_flow(), self.tech)
        self.assertEqual((row['done'], row['target_met'], row['target_pct']), (2, True, 100))

    def test_a_person_with_a_target_and_nothing_done_is_a_row_of_zeros(self):
        self.Target.create({'date': self.today, 'user_id': self.idle.id, 'target': 4})
        flow = self.env['lab.flow'].get_flow()
        row = self._row(flow, self.idle)
        self.assertTrue(row, "a target with nothing against it is what the manager looks for")
        self.assertEqual((row['taken'], row['finished'], row['done'], row['target']),
                         (0, 0, 0, 4))
        self.assertFalse(row['target_met'])
        summary = flow['targets']
        self.assertGreaterEqual(summary['set'], 1)
        self.assertGreaterEqual(summary['target'], 4)
        self.assertLessEqual(summary['met'], summary['set'])

    def test_a_person_with_no_target_is_not_judged(self):
        self._job_today(self.tech)
        row = self._row(self.env['lab.flow'].get_flow(), self.tech)
        self.assertEqual((row['target'], row['done'], row['target_met']), (0, 0, False))

    # ------------------------------------------------------------ station board
    def test_only_a_manager_may_set_targets_from_the_board(self):
        Station = self.env['lab.station']
        board = Station.with_user(self.lead).get_station(self.bench.id)
        self.assertFalse(board['can_set_targets'], "a station lead is not the manager")
        with self.assertRaises(AccessError):
            Station.with_user(self.lead).set_targets(
                self.bench.id, fields.Date.to_string(self.today), {self.tech.id: 5})
        self.assertFalse(self.Target.search([('user_id', '=', self.tech.id)]))

    def test_the_board_sets_bench_targets_and_a_zero_takes_one_away(self):
        Station = self.env['lab.station'].with_user(self.boss)
        self._job_today(self.tech)
        board = Station.set_targets(self.bench.id, fields.Date.to_string(self.today),
                                    {str(self.tech.id): 4, str(self.idle.id): 3,
                                     str(self.lead.id): 0})
        rows = self.Target.search([('date', '=', self.today),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual({r.user_id.id: r.target for r in rows},
                         {self.tech.id: 4, self.idle.id: 3})
        self.assertEqual(rows.filtered(lambda r: r.user_id == self.tech).done_count, 1,
                         "the rows are counted before the board comes back")
        line = board['bench_day']
        self.assertEqual((line['target'], line['done_against'], line['with_target']),
                         (7, 1, 2))
        chip = next(p for p in board['bench_people'] if p['id'] == self.tech.id)
        self.assertEqual((chip['target'], chip['day_count']), (4, 1))

        # Change one, take one away, and the row count follows.
        board = Station.set_targets(self.bench.id, fields.Date.to_string(self.today),
                                    {str(self.tech.id): 6, str(self.idle.id): 0})
        rows = self.Target.search([('date', '=', self.today),
                                   ('workcenter_id', '=', self.bench.id)])
        self.assertEqual({r.user_id.id: r.target for r in rows}, {self.tech.id: 6})
        self.assertEqual(board['bench_day']['target'], 6)

    def test_a_bench_you_do_not_run_refuses_its_targets(self):
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.boss).set_targets(
                self.other.id, fields.Date.to_string(self.today),
                {str(self.tech.id): 5})

    def test_the_board_only_sets_targets_for_the_benchs_own_people(self):
        stranger = self.env['res.users'].create({
            'name': 'Board Stranger', 'login': 'brd_stranger',
            'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        self.env['lab.station'].with_user(self.boss).set_targets(
            self.bench.id, fields.Date.to_string(self.today), {str(stranger.id): 9})
        self.assertFalse(self.Target.search([('user_id', '=', stranger.id)]),
                         "somebody not posted here gets no target from this bench")

    def test_the_board_sets_targets_for_the_day_being_looked_at(self):
        yesterday = self.today - timedelta(days=1)
        board = self.env['lab.station'].with_user(self.boss).set_targets(
            self.bench.id, fields.Date.to_string(yesterday), {str(self.tech.id): 2})
        self.assertEqual(board['done_day'], fields.Date.to_string(yesterday),
                         "the board comes back on the same day it was set for")
        row = self.Target.search([('user_id', '=', self.tech.id)])
        self.assertEqual((row.date, row.target), (yesterday, 2))


@tagged('post_install', '-at_install')
class TestFinisherCounts(TransactionCase):
    """Two people work on a job at a finishing bench, and both count it.

    Scoped to its own bench and its own people: this database has other
    technicians with stamps today.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.maker = cls.env['res.users'].create({
            'name': 'Count Maker', 'login': 'cnt_maker', 'group_ids': [(4, mrp_user)]})
        cls.polisher = cls.env['res.users'].create({
            'name': 'Count Polisher', 'login': 'cnt_polisher', 'group_ids': [(4, mrp_user)]})
        cls.lead = cls.env['res.users'].create({
            'name': 'Count Lead', 'login': 'cnt_lead', 'group_ids': [(4, mrp_user)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Count Bench', 'is_finisher': True,
            'users': [(6, 0, (cls.maker | cls.polisher).ids)],
            'head_user_ids': [(6, 0, cls.lead.ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Count Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Polish', 'workcenter_id': cls.bench.id})]})
        cls.Target = cls.env['lab.work.target']
        cls.today = cls.env['lab.station']._lab_today()

    def _job(self, technician, finisher=None, handed=True):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        now = fields.Datetime.now()
        vals = {'workcenter_id': self.bench.id, 'bench_user_id': technician.id,
                'bench_assigned_at': now, 'accepted_at': now}
        if handed:
            vals['handed_over_at'] = now
        if finisher:
            vals['finisher_user_id'] = finisher.id
        wo.write(vals)
        return wo

    def _counts(self):
        return self.env['lab.station']._day_counts(
            self.bench, self.maker | self.polisher, self.today)

    def test_a_finisher_is_credited_with_the_job_they_finished(self):
        self._job(self.maker, self.polisher)
        counts = self._counts()
        self.assertEqual(counts.get(self.maker.id), 1, "the technician did it")
        self.assertEqual(counts.get(self.polisher.id), 1, "the polisher finished it")

    def test_a_job_one_person_did_and_finished_counts_once(self):
        self._job(self.maker, self.maker)
        self.assertEqual(self._counts().get(self.maker.id), 1,
                         "one job, not two, however many roles carry the same name")

    def test_an_unfinished_job_credits_only_the_technician(self):
        self._job(self.maker, handed=False)
        counts = self._counts()
        self.assertEqual(counts.get(self.maker.id), 1)
        self.assertFalse(counts.get(self.polisher.id),
                         "nobody has finished it, so nobody is credited for finishing it")

    def test_the_target_counts_what_the_chip_counts(self):
        self._job(self.maker, self.polisher)
        row = self.Target.create({
            'date': self.today, 'user_id': self.polisher.id,
            'workcenter_id': self.bench.id, 'target': 1})
        row.action_refresh()
        self.assertEqual(row.done_count, 1)
        self.assertTrue(row.met, "the polisher reached a target of one by finishing one")
        chip = next(p for p in self.env['lab.station']._bench_load(self.bench, self.today)
                    if p['id'] == self.polisher.id)
        self.assertEqual((chip['day_count'], chip['target'], chip['target_met']),
                         (row.done_count, 1, True))

    def test_a_finishers_day_is_yesterdays_job_on_the_day_it_was_handed_on(self):
        """A job given out yesterday and finished today counts for the finisher
        today, because the day it was handed on is the day their name went on it."""
        wo = self._job(self.maker, self.polisher)
        yesterday = fields.Datetime.now() - timedelta(days=1)
        wo.write({'bench_assigned_at': yesterday, 'accepted_at': yesterday})
        counts = self._counts()
        self.assertEqual(counts.get(self.polisher.id), 1)
        self.assertEqual(counts.get(self.maker.id), 1, "handed on today counts today")

    def test_the_filtered_column_shows_what_the_count_counted(self):
        wo = self._job(self.maker, self.polisher)
        rows, total = self.env['lab.station']._done_on(
            self.bench, self.today, self.polisher.id)
        self.assertEqual(total, 1)
        self.assertEqual(rows.id, wo.id,
                         "filtering to the polisher shows the job they finished")

    def test_the_flow_board_credits_both_and_splits_the_time(self):
        self._job(self.maker, self.polisher)
        flow = self.env['lab.flow'].get_flow()
        rows = {p['id']: p for p in flow['people']}
        self.assertEqual(rows[self.maker.id]['finished'], 1)
        self.assertEqual(rows[self.polisher.id]['finished'], 1)
        self.assertEqual(rows[self.polisher.id]['finished_as_finisher'], 1)
        self.assertEqual(rows[self.polisher.id]['taken'], 0,
                         "they did not take it, they finished it")
        self.assertEqual(rows[self.maker.id]['finished_as_finisher'], 0)

    def test_a_persons_drill_opens_both_kinds_of_work(self):
        wo = self._job(self.maker, self.polisher)
        action = self.env['lab.flow'].action_open_person(self.polisher.id)
        self.assertIn(wo, self.env['mrp.workorder'].search(action['domain']))
        report = self.env['lab.mrp.report'].open_person_cases(
            self.polisher.id, fields.Date.to_string(self.today),
            fields.Date.to_string(self.today))
        self.assertIn(wo, self.env['mrp.workorder'].search(report['domain']))

    # ------------------------------------------------------------ the picker
    def test_the_station_list_says_which_bench_is_which(self):
        board = self.env['lab.station'].with_user(self.lead).get_station(self.bench.id)
        mine = next(s for s in board['stations'] if s['id'] == self.bench.id)
        self.assertEqual(mine['name'], self.bench.display_name)
        self.assertTrue(mine['is_lead'], "the reader runs this bench")
        self.assertIn('code', mine, "the code printed on the bench")
        for key in ('waiting', 'bench', 'finished'):
            self.assertIn(key, mine, "where the work at this bench is")

    def test_the_station_list_says_where_the_work_is(self):
        """Waiting, on the bench and finished - not one pile called "waiting"."""
        Station = self.env['lab.station']
        waiting = self._job(self.maker, handed=False)
        waiting.accepted_at = False
        taken = self._job(self.maker, handed=False)
        taken.write({'accepted_at': fields.Datetime.now()})
        self._job(self.maker, self.polisher)          # handed on today

        board = Station.with_user(self.lead).get_station(self.bench.id)
        mine = next(s for s in board['stations'] if s['id'] == self.bench.id)
        self.assertEqual(mine['waiting'], 1, "one nobody has taken")
        self.assertEqual(mine['bench'], 1, "one accepted and not passed on")
        self.assertEqual(mine['finished'], 1, "one passed on today")
        self.assertEqual(mine['queue'], 2, "the pile is the first two")

    def test_the_station_list_follows_the_day_being_read(self):
        Station = self.env['lab.station']
        self._job(self.maker, self.polisher)          # handed on today
        yesterday = Station._lab_today() - timedelta(days=1)
        answer = Station.with_user(self.lead).done_on(
            self.bench.id, fields.Date.to_string(yesterday))
        self.assertIn('stations', answer, "the picker moves with the day")
        mine = next(s for s in answer['stations'] if s['id'] == self.bench.id)
        self.assertEqual(mine['finished'], 0, "nothing was passed on yesterday")
        self.assertGreaterEqual(mine['waiting'] + mine['bench'], 0)


@tagged('post_install', '-at_install')
class TestMineOnTheTiles(TransactionCase):
    """"My work orders" on a filter tile counts both roles.

    Only runs where the tiles module is installed; the rule itself is the work
    order's, and is the same one the boards count by.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.maker = cls.env['res.users'].create({
            'name': 'Tile Maker', 'login': 'tile_maker', 'group_ids': [(4, mrp_user)]})
        cls.polisher = cls.env['res.users'].create({
            'name': 'Tile Polisher', 'login': 'tile_polisher', 'group_ids': [(4, mrp_user)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Tile Bench', 'is_finisher': True,
            'users': [(6, 0, (cls.maker | cls.polisher).ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Tile Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Polish', 'workcenter_id': cls.bench.id})]})

    def _job(self, technician, finisher=None, handed=True):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[0]
        now = fields.Datetime.now()
        wo.write({'workcenter_id': self.bench.id, 'bench_user_id': technician.id,
                  'bench_assigned_at': now, 'accepted_at': now,
                  'handed_over_at': now if handed else False,
                  'finisher_user_id': finisher.id if finisher else False})
        return wo

    def _mine_tile(self, name):
        """A tile counting "my work orders" at this bench, as a manager sets it."""
        field = self.env['ir.model.fields'].sudo().search([
            ('model', '=', 'mrp.workorder'), ('name', '=', 'bench_user_id')], limit=1)
        return self.env['filter.tile'].create({
            'name': name, 'only_mine': True, 'user_field_id': field.id,
            'model_id': self.env['ir.model']._get('mrp.workorder').id,
            'domain': "[('workcenter_id', '=', %s)]" % self.bench.id})

    def test_the_work_order_answers_with_both_roles(self):
        Workorder = self.env['mrp.workorder']
        leaf = Workorder._dft_mine_leaf('bench_user_id', self.polisher.id)
        self.assertEqual(leaf, ['|', ('bench_user_id', 'in', [self.polisher.id]),
                                ('finisher_user_id', 'in', [self.polisher.id])])
        self.assertEqual(Workorder._dft_mine_leaf('finisher_user_id', self.polisher.id), leaf,
                         "either field asks the same question")
        self.assertEqual(Workorder._dft_mine_leaf('create_uid', self.polisher.id),
                         [('create_uid', '=', self.polisher.id)],
                         "another user field is left alone")

    def test_a_polishers_tile_counts_the_jobs_they_finished(self):
        if 'filter.tile' not in self.env:
            self.skipTest("the tiles module is not installed")
        wo = self._job(self.maker, self.polisher)
        tile = self._mine_tile('My work')
        # The registry is read by whoever is looking, so the payload - and the
        # resolved "and it is mine" leaf inside it - belongs to that viewer.
        payload = tile.with_user(self.polisher)._tile_payload()

        counted = self.env['filter.tile'].with_user(self.polisher).compute_tiles(
            'mrp.workorder', [payload])
        self.assertEqual(counted['tiles'][payload['key']]['count'], 1,
                         "the job they finished is theirs to count")

        # The filter the browser applies is the same question, so the list
        # behind the tile holds the same job the tile counted.
        self.assertTrue(payload['mine_domain'], "the payload carries the resolved leaf")
        found = self.env['mrp.workorder'].search(
            [('workcenter_id', '=', self.bench.id)] + payload['mine_domain'])
        self.assertIn(wo, found)

    def test_the_maker_still_counts_their_own(self):
        if 'filter.tile' not in self.env:
            self.skipTest("the tiles module is not installed")
        self._job(self.maker, self.polisher)
        tile = self._mine_tile('My work too')
        counted = self.env['filter.tile'].with_user(self.maker).compute_tiles(
            'mrp.workorder', [tile.with_user(self.maker)._tile_payload()])
        self.assertEqual(counted['tiles'][str(tile.id)]['count'], 1,
                         "widening what mine means did not cost the maker their own")

    def test_a_job_nobody_finished_belongs_to_its_technician_alone(self):
        if 'filter.tile' not in self.env:
            self.skipTest("the tiles module is not installed")
        self._job(self.maker)
        tile = self._mine_tile('Unfinished')
        for user, expected in ((self.maker, 1), (self.polisher, 0)):
            payload = tile.with_user(user)._tile_payload()
            counted = self.env['filter.tile'].with_user(user).compute_tiles(
                'mrp.workorder', [payload])
            self.assertEqual(counted['tiles'][payload['key']]['count'], expected,
                             user.name)


@tagged('post_install', '-at_install')
class TestFindAtTheBench(TransactionCase):
    """A column shows forty cards; the deepest bench on this floor has nine
    hundred behind them. Finding and ordering therefore run over the whole
    queue on the server, never over the page. (client, 2026-09-10)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.lead = cls.env['res.users'].create({
            'name': 'Find Lead', 'login': 'find_lead', 'group_ids': [(4, mrp_user)]})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Find Bench', 'head_user_ids': [(6, 0, cls.lead.ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Find Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend', 'workcenter_id': cls.bench.id})]})
        cls.clinic = cls.env['res.partner'].create({'name': 'DR FIND KURIAKOSE'})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'DR OTHER'})

    def _case(self, patient, clinic=None, priority=False):
        order = self.env['sale.order'].create({
            'partner_id': (clinic or self.clinic).id, 'patient': patient})
        if priority:
            order.priority = priority
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id,
            'product_qty': 1.0})
        mo.action_confirm()
        # `sale_id` is a stored related through sale_line_id, so a fixture writes
        # it by SQL rather than inventing a whole sale-to-manufacture chain.
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            [order.id, mo.id])
        self.env.cr.execute("UPDATE mrp_workorder SET sale_id = %s WHERE production_id = %s",
                            [order.id, mo.id])
        mo.invalidate_recordset()
        mo.workorder_ids.invalidate_recordset()
        return mo.workorder_ids[0]

    def _board(self, **kw):
        return self.env['lab.station'].with_user(self.lead).get_station(
            self.bench.id, **kw)

    def _ids(self, board, column='incoming'):
        return [c['id'] for c in board[column]]

    # ------------------------------------------------------------ finding
    def test_a_patient_is_found_in_the_whole_queue(self):
        wanted = self._case('MEERA ANTONY')
        self._case('SOMEBODY ELSE')
        board = self._board(query='meera')
        self.assertEqual(self._ids(board), [wanted.id])
        self.assertEqual(board['totals']['incoming'], 1,
                         "the count follows the search, not the page")
        self.assertEqual(board['query'], 'meera')

    def test_a_doctor_is_found_too(self):
        mine = self._case('A', self.clinic)
        self._case('B', self.other_clinic)
        self.assertEqual(self._ids(self._board(query='KURIAKOSE')), [mine.id])

    def test_a_case_number_is_found_without_its_prefix(self):
        wanted = self._case('NUMBERED')
        digits = wanted.production_id.name.split('/')[-1]
        self.assertIn(wanted.id, self._ids(self._board(query=digits)))

    def test_nothing_typed_shows_the_whole_bench(self):
        self._case('ONE')
        self._case('TWO')
        self.assertEqual(len(self._ids(self._board())), 2)
        self.assertEqual(self._board()['query'], '')

    def test_a_search_that_matches_nothing_says_so_rather_than_lying(self):
        self._case('ONE')
        board = self._board(query='ZZZ NOBODY')
        self.assertEqual(self._ids(board), [])
        self.assertEqual(board['totals']['incoming'], 0)

    # ------------------------------------------------------------ ordering
    def test_urgent_first_reaches_an_urgent_job_behind_the_page(self):
        plain = [self._case('PLAIN %s' % i) for i in range(3)]
        urgent = self._case('URGENT ONE', priority='urgent')
        # The urgent case is the NEWEST, so oldest-first puts it last.
        self.assertEqual(self._ids(self._board())[-1], urgent.id)
        self.assertEqual(self._ids(self._board(sort='urgent'))[0], urgent.id,
                         "urgent first means urgent first, wherever it stands")
        self.assertEqual(len(self._ids(self._board(sort='urgent'))), len(plain) + 1,
                         "and the rest of the queue still fills in behind it")

    def test_newest_first_is_the_other_end_of_the_same_queue(self):
        first = self._case('FIRST')
        last = self._case('LAST')
        self.assertEqual(self._ids(self._board(sort='newest'))[0], last.id)
        self.assertEqual(self._ids(self._board(sort='oldest'))[0], first.id)

    def test_an_unknown_order_falls_back_to_the_labs_own_rule(self):
        first = self._case('FIRST')
        self._case('SECOND')
        board = self._board(sort='nonsense')
        self.assertEqual(self._ids(board)[0], first.id)
        self.assertEqual(board['sort'], 'nonsense',
                         "the board says what it was asked for")

    # ------------------------------------------------------------ ageing
    def test_a_card_says_how_long_it_has_stood(self):
        wo = self._case('AGED')
        card = self._board()['incoming'][0]
        self.assertEqual(card['id'], wo.id)
        self.assertIn('here_days', card)
        self.assertEqual(card['case_days'], 0, "raised today")
        self.assertFalse(card['stale'])

    def test_a_job_that_has_stood_for_days_is_flagged(self):
        wo = self._case('OLD')
        self.env.cr.execute(
            "UPDATE mrp_production SET create_date = now() - interval '5 days' WHERE id = %s",
            [wo.production_id.id])
        wo.production_id.invalidate_recordset()
        card = self._board()['incoming'][0]
        self.assertGreaterEqual(card['case_days'], 5)
        self.assertTrue(card['stale'], "five days at one bench is what a lead walks over for")


@tagged('post_install', '-at_install')
class TestWholeDayTargetIsCountedAcrossBenches(TransactionCase):
    """A target set for no particular bench is answered by every bench.

    Technicians here move between benches during a day. A target set for the
    day as a whole was still being held up against the count at whichever
    bench the board was showing, so somebody who did the work in two places
    read as short in both - on a day they had beaten the number.
    (client, 2026-09-12)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Target = cls.env['lab.work.target']
        cls.Station = cls.env['lab.station']
        cls.today = cls.env['lab.station']._lab_today()
        cls.tech = cls.env['res.users'].create({
            'name': 'Roaming Tech', 'login': 'roam_tech',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)]})
        cls.here = cls.env['mrp.workcenter'].create({
            'name': 'Roam Bench A', 'users': [(6, 0, cls.tech.ids)]})
        cls.there = cls.env['mrp.workcenter'].create({
            'name': 'Roam Bench B', 'users': [(6, 0, cls.tech.ids)]})
        cls.product = cls.env['product.product'].create(
            {'name': 'Roam Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Bend',
                                      'workcenter_id': cls.here.id})]})

    def _did(self, workcenter, n=1, handed=False):
        """`n` jobs taken by the technician at `workcenter` today."""
        out = []
        for _i in range(n):
            mo = self.env['mrp.production'].create({
                'product_id': self.product.id, 'bom_id': self.bom.id,
                'product_qty': 1.0})
            mo.action_confirm()
            wo = mo.workorder_ids[0]
            vals = {'workcenter_id': workcenter.id, 'bench_user_id': self.tech.id,
                    'bench_assigned_at': fields.Datetime.now()}
            if handed:
                vals['handed_over_at'] = fields.Datetime.now()
            wo.write(vals)
            out.append(wo)
        return out

    def _chip(self, workcenter):
        rows = self.Station._bench_load(workcenter, self.today)
        return next(r for r in rows if r['id'] == self.tech.id)

    def test_a_whole_day_target_counts_the_work_done_at_every_bench(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 5})           # no workcenter_id: the day
        self._did(self.here, 2)
        self._did(self.there, 3)
        chip = self._chip(self.here)
        self.assertEqual(chip['day_count'], 5, "two here and three there is five")
        self.assertEqual(chip['target'], 5)
        self.assertTrue(chip['target_met'], "the number was beaten, not missed")
        self.assertTrue(chip['all_benches'], "and the chip says where it came from")
        self.assertEqual(chip['bench_count'], 2, "of which two are this bench's")

    def test_a_bench_target_is_still_answered_by_that_bench_alone(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.here.id, 'target': 5})
        self._did(self.here, 2)
        self._did(self.there, 3)
        chip = self._chip(self.here)
        self.assertEqual(chip['day_count'], 2, "this bench's target, this bench's count")
        self.assertFalse(chip['all_benches'])
        self.assertFalse(chip['target_met'])

    def test_the_bench_target_wins_where_both_are_set(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 5})
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.here.id, 'target': 2})
        self._did(self.here, 2)
        self._did(self.there, 3)
        chip = self._chip(self.here)
        self.assertEqual((chip['target'], chip['day_count']), (2, 2))
        self.assertFalse(chip['all_benches'], "asked about this bench, answered by it")

    def test_the_filter_opens_the_same_work_the_number_counted(self):
        """Clicking the chip must not show fewer jobs than the chip said."""
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 5})
        self._did(self.here, 2, handed=True)
        self._did(self.there, 3, handed=True)
        _rows, total = self.Station._done_on(self.here, self.today, self.tech.id)
        self.assertEqual(total, 5, "the whole floor, like the chip")

    def test_a_bench_targets_filter_stays_at_its_bench(self):
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'workcenter_id': self.here.id, 'target': 5})
        self._did(self.here, 2, handed=True)
        self._did(self.there, 3, handed=True)
        _rows, total = self.Station._done_on(self.here, self.today, self.tech.id)
        self.assertEqual(total, 2)

    def test_a_zero_target_is_no_target(self):
        """Set Daily Targets writes 0 for everybody left blank. A zero must not
        switch the chip to counting across benches, or every person on the
        floor wears the globe. (client, 2026-09-14)"""
        self.Target.create({'date': self.today, 'user_id': self.tech.id,
                            'target': 0})
        self._did(self.here, 2)
        self._did(self.there, 3)
        chip = self._chip(self.here)
        self.assertEqual(chip['day_count'], 2, "this bench, as with no target")
        self.assertFalse(chip['all_benches'], "no globe for a blank")
        self.assertFalse(chip['target'])

    def test_somebody_with_no_target_at_all_is_unchanged(self):
        self._did(self.here, 2)
        self._did(self.there, 3)
        chip = self._chip(self.here)
        self.assertEqual(chip['day_count'], 2, "no target, so this bench as before")
        self.assertFalse(chip['all_benches'])

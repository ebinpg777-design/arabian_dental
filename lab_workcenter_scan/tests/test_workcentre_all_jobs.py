# -*- coding: utf-8 -*-
"""The whole history of a bench, not just its queue. (client, 2026-09-26)

Three screens said something about a work centre and none of them answered
"what has this bench done": "Jobs Here" is the open queue, core's own
workorder_count is the same thing under a different name, and the flow board's
drill narrows harder still - live orders only, deliberately, so the drill holds
exactly the work the number counted.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkcentreAllJobs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'History Bench'})
        cls.product = cls.env['product.product'].create({
            'name': 'History Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Step', 'workcenter_id': cls.bench.id})],
        })

    def _job(self, finish=False):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        if finish:
            mo.workorder_ids.write({'state': 'done'})
        return mo

    def test_the_two_counts_say_different_things(self):
        self._job()
        self._job(finish=True)
        self.bench.invalidate_recordset()
        self.assertEqual(self.bench.open_workorder_count, 1, "one still open")
        self.assertEqual(self.bench.all_workorder_count, 2, "both, ever")

    def test_the_all_count_is_not_cores_field(self):
        """Core owns `workorder_count` on this model and it is the OPEN count,
        computed together with workcenter_load and the late and blocked counts.
        Taking that name would have taken those three down with it, silently."""
        self._job()
        self._job(finish=True)
        self.bench.invalidate_recordset()
        self.assertEqual(self.bench.workorder_count, self.bench.open_workorder_count)
        self.assertNotEqual(self.bench.workorder_count, self.bench.all_workorder_count)

    def test_core_still_computes_its_own_numbers(self):
        """The name clash would not have raised; it would just have left these
        wrong. So they are asserted."""
        self._job()
        self.bench.invalidate_recordset()
        for field in ('workcenter_load', 'workorder_late_count',
                      'workorder_blocked_count', 'workorder_ready_count'):
            self.assertIsNotNone(self.bench[field], field)

    def test_the_action_holds_every_job_including_the_finished_ones(self):
        open_job = self._job()
        done_job = self._job(finish=True)
        action = self.bench.action_view_all_workorders()
        found = self.env['mrp.workorder'].search(action['domain'])
        self.assertIn(open_job.workorder_ids, found)
        self.assertIn(done_job.workorder_ids, found)

    def test_the_queue_action_still_holds_only_the_open_ones(self):
        open_job = self._job()
        done_job = self._job(finish=True)
        action = self.bench.action_view_open_workorders()
        found = self.env['mrp.workorder'].search(action['domain'])
        self.assertIn(open_job.workorder_ids, found)
        self.assertNotIn(done_job.workorder_ids, found)

    def test_the_full_list_is_grouped_by_status(self):
        """Otherwise it is 4,388 rows to scroll. The filter is called `status`
        and lives only in core's own work-order search view, so that view is
        pinned - a guessed search_default does nothing, silently."""
        action = self.bench.action_view_all_workorders()
        self.assertEqual(action['context'].get('search_default_status'), 1)
        self.assertTrue(action['search_view_id'], "the view that defines it")
        search = self.env.ref('mrp.view_mrp_production_workorder_form_view_filter')
        self.assertEqual(action['search_view_id'][0], search.id)
        self.assertIn('search_default_status'.replace('search_default_', ''),
                      search.arch_db, "the filter really is named that")

    def test_both_lists_are_named_by_case(self):
        """The title already says which bench, so the columns say which case."""
        view = self.env.ref('lab_workcenter_scan.view_workorder_list_case')
        for action in (self.bench.action_view_all_workorders(),
                       self.bench.action_view_open_workorders()):
            self.assertEqual(action['views'][0][0], view.id)

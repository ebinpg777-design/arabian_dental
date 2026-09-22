# -*- coding: utf-8 -*-
"""A job with steps left is never closeable, however its state got computed."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProductionToClose(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Test Appliance', 'is_storable': True})
        cls.component = cls.env['product.product'].create({
            'name': 'Test Wire', 'is_storable': True})
        cls.workcenters = cls.env['mrp.workcenter'].create([
            {'name': 'Bench A'}, {'name': 'Bench B'}, {'name': 'Bench C'}])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'bom_line_ids': [(0, 0, {'product_id': cls.component.id, 'product_qty': 1.0})],
            'operation_ids': [
                (0, 0, {'name': 'Step %s' % i, 'workcenter_id': wc.id})
                for i, wc in enumerate(cls.workcenters, start=1)
            ],
        })

    def _new_mo(self):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        return mo

    def test_first_step_does_not_close_the_job(self):
        """Accepting and finishing step one leaves two steps, so nothing may close."""
        mo = self._new_mo()
        self.assertEqual(len(mo.workorder_ids), 3)

        first = mo.workorder_ids.sorted('sequence')[0]
        first.button_start(raise_on_invalid_state=False)
        self.assertNotEqual(
            mo.state, 'to_close',
            "accepting the first step must not make the job closeable")

        first.button_finish()
        self.assertEqual(first.state, 'done')
        self.assertNotEqual(
            mo.state, 'to_close',
            "finishing the first step must not make the job closeable while "
            "%s step(s) are still open" % len(mo.workorder_ids.filtered(
                lambda w: w.state not in ('done', 'cancel'))))
        self.assertEqual(mo.state, 'progress')

    def test_even_the_last_step_does_not_close_the_job(self):
        """A case is closed by its DELIVERY, not by the last bench.

        This asserted `to_close` until 2026-09-09, when the lab asked for the
        opposite: nothing here ever moved a job OUT of To Close - the picking
        closes its jobs when they go out - so a finished job sat in a state the
        scanning board reads as finished and will not show. It stays in
        progress instead, and the delivery closes it.
        """
        mo = self._new_mo()
        for wo in mo.workorder_ids.sorted('sequence'):
            wo.button_start(raise_on_invalid_state=False)
            wo.button_finish()
        self.assertTrue(all(w.state == 'done' for w in mo.workorder_ids))
        self.assertEqual(mo.state, 'progress')
        self.assertNotEqual(mo.state, 'to_close')

    def test_a_job_that_is_only_a_quantity_is_left_to_core(self):
        """No steps, no rule of ours: core closes it when the quantity is made."""
        plain = self.env['product.product'].create({
            'name': 'Test Bracket', 'is_storable': True})    # no bill of materials
        mo = self.env['mrp.production'].create({
            'product_id': plain.id, 'product_qty': 1.0})
        mo.action_confirm()
        self.assertFalse(mo.workorder_ids)
        mo.qty_producing = mo.product_qty
        mo.invalidate_recordset(['state'])
        self.assertEqual(mo.state, 'to_close')

    def test_the_repair_pass_empties_to_close(self):
        """Rows already written down are corrected too - the migration's job."""
        mo = self._new_mo()
        for wo in mo.workorder_ids.sorted('sequence'):
            wo.button_start(raise_on_invalid_state=False)
            wo.button_finish()
        # _strand, not a bare UPDATE: a row with a recompute still queued heals
        # itself the moment the repair pass calls `search`, and the test then
        # proves nothing. (see the helper)
        self._strand(mo)
        moved = self.env['mrp.production']._lab_repair_premature_to_close([mo.id])
        self.assertEqual(moved, mo, "the stranded row is one it moves")
        # The repair writes through the ORM; the row itself is only proof once
        # that write has reached the table.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT state FROM mrp_production WHERE id=%s", (mo.id,))
        self.assertEqual(self.env.cr.fetchone()[0], 'progress',
                         "and the row itself is rewritten, not just the reading")

    def test_guard_corrects_a_bad_compute(self):
        """Even if something forces 'to_close', recomputing takes it back."""
        mo = self._new_mo()
        first = mo.workorder_ids.sorted('sequence')[0]
        first.button_start(raise_on_invalid_state=False)
        mo.invalidate_recordset()
        self.env.cr.execute(
            "UPDATE mrp_production SET state='to_close' WHERE id=%s", (mo.id,))
        mo.invalidate_recordset()
        self.assertEqual(mo.state, 'to_close')
        mo._compute_state()
        self.assertNotEqual(mo.state, 'to_close')

    def _strand(self, mo):
        """Store the row exactly as the floor's two jobs were stored.

        Not just a dirty cache: `flush_all` first so no recompute is left queued, and
        `invalidate_all` after so the value is read back from the table. A row that
        merely has a pending recompute heals itself the moment anything calls `search`
        — which is why an earlier version of these tests passed against code that could
        not have fixed MO/284110. (client, 2026-09-09)
        """
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mrp_production SET state='to_close' WHERE id=%s", (mo.id,))
        self.env.invalidate_all()
        self.assertEqual(mo.state, 'to_close', "the row must really be stranded")
        return mo

    def test_a_stranded_job_can_still_be_scanned(self):
        """The bug the floor hit: two steps done, and the next one will not start.

        `button_start` propagates the start moment to the order, `mrp.production.write`
        reads a bare `date_start` as a reschedule and unplans, and unplanning refuses
        once a step is finished — so the scan died with "Some work orders are already
        done, so you cannot unplan this manufacturing order." (client, 2026-09-09)
        """
        mo = self._new_mo()
        steps = mo.workorder_ids.sorted('sequence')
        steps[0].button_start(raise_on_invalid_state=False)
        steps[0].button_finish()
        self.assertEqual(steps[0].state, 'done')
        self.assertTrue(mo.is_planned, "starting a step is what plans the job")
        self._strand(mo)

        # No raise. Before the fix this was the UserError above.
        steps[1].button_start(raise_on_invalid_state=False)
        self.assertEqual(steps[1].state, 'progress')
        self.assertTrue(mo.is_planned, "the job must keep its plan, not lose it")

    def test_the_repair_pass_releases_a_stranded_job(self):
        """A row already stored wrong is put right, because no recompute is coming."""
        mo = self._new_mo()
        steps = mo.workorder_ids.sorted('sequence')
        steps[0].button_start(raise_on_invalid_state=False)
        steps[0].button_finish()
        self._strand(mo)

        moved = self.env['mrp.production']._lab_repair_premature_to_close([mo.id])
        self.assertIn(mo, moved)
        self.assertEqual(mo.state, 'progress')

    def test_the_scan_releases_a_stranded_job(self):
        """The floor's own route in: resolving a scanned code heals the job."""
        mo = self._new_mo()
        steps = mo.workorder_ids.sorted('sequence')
        steps[0].button_start(raise_on_invalid_state=False)
        steps[0].button_finish()
        self._strand(mo)

        Station = self.env['lab.station']
        found = Station._workorder_from_code(mo.name, steps[1].workcenter_id)
        self.assertEqual(found, steps[1], "the scan must land on the open step")
        self.assertEqual(mo.state, 'progress', "and the job must be workable again")

    def test_the_repair_pass_takes_a_finished_job_out_of_to_close_too(self):
        """A job with steps is never To Close here, whichever step was the last.

        This asserted the opposite until 2026-09-09 — "a finished job must stay
        closeable" — because that was core's rule and nothing had said otherwise.
        The lab has now said otherwise: the DELIVERY closes a case, so a job
        finished at the last bench waits in progress until it goes out.
        """
        mo = self._new_mo()
        for wo in mo.workorder_ids.sorted('sequence'):
            wo.button_start(raise_on_invalid_state=False)
            wo.button_finish()
        self.assertEqual(mo.state, 'progress')
        self._strand(mo)
        moved = self.env['mrp.production']._lab_repair_premature_to_close([mo.id])
        self.assertEqual(moved, mo, "a stranded finished job is moved as well")
        # The repair writes through the ORM; the row itself is only proof once
        # that write has reached the table.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT state FROM mrp_production WHERE id=%s", (mo.id,))
        self.assertEqual(self.env.cr.fetchone()[0], 'progress')

    def test_the_compute_sees_steps_the_relation_has_lost(self):
        """The misfire itself: `workorder_ids` reads empty, the rows are still there."""
        mo = self._new_mo()
        steps = mo.workorder_ids.sorted('sequence')
        steps[0].button_start(raise_on_invalid_state=False)
        self.assertEqual(len(mo._lab_step_states()[mo.id]), 3,
                         "read through the live relation")

        # The blank reading that fools core, reproduced. The steps are parked on
        # another job (`production_id` is NOT NULL, so they cannot simply be orphaned),
        # the empty relation is read into cache, and then they are put back WITHOUT
        # invalidating — leaving cache blank while the table holds all three.
        other = self._new_mo()
        step_ids = tuple(steps.ids)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mrp_workorder SET production_id=%s WHERE id IN %s",
            (other.id, step_ids))
        self.env.invalidate_all()
        self.assertFalse(mo.workorder_ids, "the relation now reads empty")
        self.env.cr.execute(
            "UPDATE mrp_workorder SET production_id=%s WHERE id IN %s",
            (mo.id, step_ids))
        self.assertFalse(mo.workorder_ids, "cache still blank, table now correct")

        self.assertEqual(
            len(mo._lab_step_states().get(mo.id, [])), 3,
            "the steps must be found in SQL when the relation has gone blank")

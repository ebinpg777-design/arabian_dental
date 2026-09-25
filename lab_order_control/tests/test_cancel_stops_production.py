# -*- coding: utf-8 -*-
"""Cancelling or re-drafting a case stops what the lab is making for it.

A corrected case is taken back with Cancel and then Set to Draft. Core cancels the
delivery transfers and leaves the manufacturing orders running, so the floor kept
building the old version while the corrected one was confirmed underneath it, and
somebody had to cancel the old MOs by hand (SO287629, 2026-09-16).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCancelStopsProduction(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Cancel Test Clinic', 'is_company': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Cancel Test Appliance', 'type': 'consu', 'list_price': 1000.0})

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })

    def _mo(self, order, state='confirmed'):
        """An MO as the floor holds it: carrying the order number in origin."""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id,
            'product_qty': 1,
            'origin': order.name,
        })
        if state != 'draft':
            mo.action_confirm()
        if state == 'progress':
            mo.qty_producing = 1
        return mo

    def test_cancelling_the_order_cancels_its_open_mos(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        order.action_cancel()
        self.assertEqual(mo.state, 'cancel')

    def test_an_mo_already_started_is_cancelled_too_and_said_so(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order, state='progress')
        order.action_cancel()
        self.assertEqual(mo.state, 'cancel')
        bodies = ' '.join(order.message_ids.mapped('body'))
        self.assertIn(mo.name, bodies, "the order says which MOs it stopped")

    def test_a_finished_mo_is_left_alone(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        mo.qty_producing = 1
        mo.button_mark_done()
        self.assertEqual(mo.state, 'done')
        order.action_cancel()
        self.assertEqual(mo.state, 'done', "a finished job is history, not something to undo")

    def test_an_mo_whose_line_was_deleted_is_still_found_by_the_order_number(self):
        """A correction deletes the order's lines, so the stock-move link dies with
        them. The order number on the MO survives, and that is what finds it.

        Lines may only be removed once the case is off Sales Order (sale_custom
        refuses otherwise), which is why the order is cancelled and drafted first -
        the same path the counter takes."""
        order = self._order()
        order.action_confirm()
        order.action_cancel()
        order.action_draft()
        order.order_line.unlink()
        self.assertFalse(order.order_line)
        stray = self._mo(order)          # left over from the first confirmation
        self.assertFalse(stray.sale_line_id)
        order.action_cancel()
        self.assertEqual(stray.state, 'cancel')

    def test_another_orders_mo_is_not_touched(self):
        order, other = self._order(), self._order()
        (order | other).action_confirm()
        mine, theirs = self._mo(order), self._mo(other)
        order.action_cancel()
        self.assertEqual(mine.state, 'cancel')
        self.assertEqual(theirs.state, 'confirmed', "only the cancelled order's work stops")

    def test_setting_a_quotation_to_draft_stops_production_too(self):
        """Set to Draft is reached through Cancel, but a draft that still has an open
        MO (an older correction, or a hand-made job) is put right on the way back."""
        order = self._order()
        order.action_confirm()
        order.action_cancel()
        late = self._mo(order)
        order.action_draft()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(late.state, 'cancel')

    def test_an_order_with_no_production_cancels_quietly(self):
        order = self._order()
        order.action_confirm()
        before = len(order.message_ids)
        order.action_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(len(order.message_ids), before,
                         "nothing to stop, nothing to say")
    # ------------------------------------------- the doors the buttons do not use
    def test_a_state_written_straight_to_cancel_stops_production_too(self):
        """`_action_cancel` is the door the buttons use and not the only one. An
        import, a server action or a data fix writing the state directly walked
        past it, and the floor kept building: four orders on staging were
        cancelled this way and kept six live jobs between them."""
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        order.write({'state': 'cancel'})
        self.assertEqual(mo.state, 'cancel')
        self.assertEqual(order.state, 'cancel')

    def test_writing_cancel_over_a_cancelled_order_does_nothing_twice(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        order.action_cancel()
        before = len(order.message_ids)
        order.write({'state': 'cancel'})
        self.assertEqual(mo.state, 'cancel')
        self.assertEqual(len(order.message_ids), before,
                         "an order already cancelled has nothing left to say")

    def test_writing_any_other_field_leaves_production_alone(self):
        """The guard is on the state, not on write: an ordinary edit of a live
        order must not stop its jobs."""
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        order.write({'client_order_ref': 'still running'})
        self.assertEqual(mo.state, 'confirmed')

    # --------------------------------------------------- putting the old ones right
    def test_the_sweep_finds_a_job_left_running_under_a_cancelled_order(self):
        """Everything cancelled before these nets existed is still out there, so
        there has to be a way to go and find it."""
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        # cancelled the way the stragglers were: no hook, no chatter
        # Flush FIRST. action_confirm leaves the order in the write queue, and
        # the next ORM search flushes that queued state='sale' straight back over
        # this UPDATE - so the sweep looks and sees a live order.
        self.env.flush_all()
        # Flush BEFORE the raw update, invalidate after: the ORM still had the
        # confirm's own write pending, and the next flush wrote 'sale' straight
        # back over the cancel this test had just put in the table.
        self.env.flush_all()
        self.env.cr.execute("UPDATE sale_order SET state = 'cancel' WHERE id = %s", (order.id,))
        order.invalidate_recordset(['state'])
        self.assertEqual(mo.state, 'confirmed', "the straggler this sweep is for")

        result = self.env['sale.order']._lab_stop_orphan_productions(orders=order)
        self.assertEqual(mo.state, 'cancel')
        self.assertIn(mo.name, result['names'])

    def test_the_sweep_can_be_run_twice(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        # Flush FIRST. action_confirm leaves the order in the write queue, and
        # the next ORM search flushes that queued state='sale' straight back over
        # this UPDATE - so the sweep looks and sees a live order.
        self.env.flush_all()
        # Flush BEFORE the raw update, invalidate after: the ORM still had the
        # confirm's own write pending, and the next flush wrote 'sale' straight
        # back over the cancel this test had just put in the table.
        self.env.flush_all()
        self.env.cr.execute("UPDATE sale_order SET state = 'cancel' WHERE id = %s", (order.id,))
        order.invalidate_recordset(['state'])
        self.env['sale.order']._lab_stop_orphan_productions(orders=order)
        again = self.env['sale.order']._lab_stop_orphan_productions(orders=order)
        self.assertEqual(mo.state, 'cancel')
        self.assertNotIn(mo.name, again['names'],
                         "a job already stopped is not in the query a second time")

    def test_the_sweep_leaves_a_live_order_alone(self):
        """The direction that matters: a sweep that cancelled production under
        orders that are still selling would empty the floor."""
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        self.env['sale.order']._lab_stop_orphan_productions(orders=order)
        self.assertEqual(mo.state, 'confirmed')

    def test_the_sweep_leaves_finished_work_alone(self):
        order = self._order()
        order.action_confirm()
        mo = self._mo(order)
        mo.qty_producing = 1
        mo.button_mark_done()
        # Flush FIRST. action_confirm leaves the order in the write queue, and
        # the next ORM search flushes that queued state='sale' straight back over
        # this UPDATE - so the sweep looks and sees a live order.
        self.env.flush_all()
        # Flush BEFORE the raw update, invalidate after: the ORM still had the
        # confirm's own write pending, and the next flush wrote 'sale' straight
        # back over the cancel this test had just put in the table.
        self.env.flush_all()
        self.env.cr.execute("UPDATE sale_order SET state = 'cancel' WHERE id = %s", (order.id,))
        order.invalidate_recordset(['state'])
        self.env['sale.order']._lab_stop_orphan_productions(orders=order)
        self.assertEqual(mo.state, 'done', "a finished job is history")

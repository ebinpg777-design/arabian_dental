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

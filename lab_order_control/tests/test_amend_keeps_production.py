# -*- coding: utf-8 -*-
"""Amending a confirmed order without losing the work done on it.

(client, 2026-11-26: "When doing Set to draft of a SO with MOs done or in progress
the working details are missing. Find a solution for amending SO which should not
affect the MOs. Product may change in the amendment. Do not block the amendment at
any stage")
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAmendKeepsProduction(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Amend Keep Clinic', 'is_company': True})
        Bench = cls.env['mrp.workcenter']
        cls.model, cls.cast, cls.mill, cls.finish = (
            Bench.create({'name': 'AK %s' % name}) for name in ('Model', 'Casting', 'Milling', 'Finishing'))
        manufacture = cls.env.ref('mrp.route_warehouse0_manufacture')
        mto = cls.env.ref('stock.route_warehouse0_mto')
        mto.active = True
        cls.routes = manufacture | mto

        def made(name, benches):
            product = cls.env['product.product'].create({
                'name': name, 'type': 'consu', 'list_price': 1000.0, 'invoice_policy': 'order',
                'taxes_id': [(5, 0, 0)], 'route_ids': [(6, 0, cls.routes.ids)]})
            cls.env['mrp.bom'].create({
                'product_tmpl_id': product.product_tmpl_id.id, 'product_qty': 1,
                'operation_ids': [(0, 0, {'name': bench.name, 'workcenter_id': bench.id,
                                          'sequence': index * 10, 'time_mode': 'manual',
                                          'time_cycle_manual': 5})
                                  for index, bench in enumerate(benches, start=1)]})
            return product

        cls.metal = made('AK Metal Crown', [cls.model, cls.cast, cls.finish])
        cls.metal_bridge = made('AK Metal Bridge', [cls.model, cls.cast, cls.finish])
        cls.zirconia = made('AK Zirconia Crown', [cls.model, cls.mill, cls.finish])

    def _order(self, product=None, qty=1):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': (product or self.metal).id,
                                   'product_uom_qty': qty})]})
        order.action_confirm()
        return order

    def _jobs(self, order):
        return self.env['mrp.production'].search(
            ['|', ('sale_id', '=', order.id), ('origin', '=', order.name)], order='id')

    def _steps(self, job):
        return job.workorder_ids.filtered(lambda w: w.state != 'cancel').sorted(
            lambda w: (w.sequence, w.id))

    def _work(self, job, how_many=1):
        """The first steps done, as the floor does them."""
        if job.state == 'draft':
            job.action_confirm()
        for step in self._steps(job)[:how_many]:
            step.button_start()
            step.button_finish()

    def _amend_and_confirm(self, order, change=None):
        order.action_amend()
        if change:
            change(order)
        order.action_confirm()

    # ------------------------------------------------------------ the fixture
    def test_confirming_raises_one_job_for_the_line(self):
        order = self._order()
        job = self._jobs(order)
        self.assertEqual(len(job), 1, "the tests below need the lab's own chain")
        self.assertEqual(job.sale_line_id, order.order_line)
        self.assertEqual(self._steps(job).mapped('workcenter_id'), self.model | self.cast | self.finish)

    # --------------------------------------------------------------- amending
    def test_an_amendment_cancels_nothing_at_any_stage(self):
        order = self._order()
        job = self._jobs(order)
        self._work(job, 2)
        self.assertEqual(job.state, 'progress')
        before = [(s.id, s.state, s.date_finished) for s in self._steps(job)]
        order.action_amend()
        self.assertEqual(order.state, 'draft')
        self.assertTrue(order.lab_amending)
        self.assertEqual(job.state, 'progress', "the job goes on")
        self.assertFalse(order.picking_ids.filtered(lambda p: p.state == 'cancel'))
        order.note = 'shade changed to A3'
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(order.lab_amending)
        self.assertEqual(self._jobs(order), job, "no second job for a line that has one")
        self.assertEqual([(s.id, s.state, s.date_finished) for s in self._steps(job)], before,
                         "the working details are exactly what they were")
        self.assertEqual(len(order.picking_ids.filtered(lambda p: p.state != 'cancel')), 1,
                         "and no second delivery")

    def test_a_finished_job_or_an_invoice_does_not_stop_it(self):
        order = self._order()
        job = self._jobs(order)
        self._work(job, 3)
        job.button_mark_done()
        self.assertEqual(job.state, 'done')
        invoice = order._create_invoices()
        invoice.action_post()
        self._amend_and_confirm(order, lambda o: o.order_line.write({'product_id': self.zirconia.id}))
        self.assertEqual(self._jobs(order), job, "what is made is made: no new job")
        self.assertEqual((job.state, job.product_id), ('done', self.metal), "history is left as it is")
        self.assertEqual(invoice.state, 'posted', "the invoice is not touched")

    # ------------------------------------------------------ the product changes
    def test_a_product_made_the_same_way_keeps_every_step(self):
        order = self._order()
        job = self._jobs(order)
        self._work(job, 1)
        steps = self._steps(job)
        self._amend_and_confirm(order, lambda o: o.order_line.write({'product_id': self.metal_bridge.id}))
        self.assertEqual(self._jobs(order), job)
        self.assertEqual(job.product_id, self.metal_bridge, "the job says what is being made now")
        self.assertEqual(job.move_finished_ids.filtered(lambda m: m.state != 'cancel').product_id,
                         self.metal_bridge)
        self.assertEqual(self._steps(job), steps, "the same steps, the same records")
        self.assertEqual(steps.mapped('state')[0], 'done')
        self.assertEqual(steps.operation_id.bom_id.product_tmpl_id, self.metal_bridge.product_tmpl_id)
        move = order.order_line.move_ids.filtered(
            lambda m: m.state != 'cancel' and m.location_dest_usage == 'customer')
        self.assertEqual(move.product_id, self.metal_bridge, "the delivery follows as well")

    def test_a_product_made_differently_keeps_the_work_and_changes_the_plan(self):
        order = self._order()
        job = self._jobs(order)
        self._work(job, 1)                              # the model is done
        model_step = self._steps(job)[0]
        self._amend_and_confirm(order, lambda o: o.order_line.write({'product_id': self.zirconia.id}))
        self.assertEqual(self._jobs(order), job)
        self.assertEqual(job.product_id, self.zirconia)
        steps = self._steps(job)
        self.assertEqual(steps.mapped('workcenter_id.name'), ['AK Model', 'AK Milling', 'AK Finishing'])
        self.assertEqual(steps[0], model_step, "the step that was done is the same record")
        self.assertEqual(steps[0].state, 'done')
        self.assertNotIn(self.cast, job.workorder_ids.workcenter_id, "no casting on a zirconia crown")
        self.assertEqual(steps[2].blocked_by_workorder_ids, steps[1], "the chain is whole")
        self.assertEqual(len(set(steps.mapped('sequence'))), 3, "no two steps share a number")

    # ------------------------------------------- quantity, lines off, lines on
    def test_the_quantity_follows(self):
        order = self._order(qty=2)
        job = self._jobs(order)
        self._work(job, 1)
        self._amend_and_confirm(order, lambda o: o.order_line.write({'product_uom_qty': 3}))
        self.assertEqual(self._jobs(order), job)
        self.assertEqual(job.product_qty, 3)

    def test_a_line_taken_off_stops_its_job_and_a_line_added_gets_one(self):
        order = self._order()
        job = self._jobs(order)
        self._work(job, 1)

        def change(o):
            o.order_line.unlink()
            o.write({'order_line': [(0, 0, {'product_id': self.zirconia.id, 'product_uom_qty': 1})]})
        self._amend_and_confirm(order, change)
        self.assertEqual(job.state, 'cancel', "what was taken off the order is not made")
        new = self._jobs(order) - job
        self.assertEqual(len(new), 1)
        self.assertEqual(new.product_id, self.zirconia)

    # -------------------------------------------------------- the Cancel button
    def test_the_cancel_button_offers_the_amendment(self):
        order = self._order()
        job = self._jobs(order)
        action = order.with_context(lab_offer_amend=True).action_cancel()
        self.assertEqual(action['res_model'], 'lab.order.cancel.choice')
        self.assertEqual(order.state, 'sale', "nothing happened yet")
        wizard = self.env['lab.order.cancel.choice'].create({'order_id': order.id})
        self.assertTrue(wizard.has_jobs)
        self.assertIn(job.name, wizard.jobs)
        wizard.action_amend()
        self.assertEqual((order.state, order.lab_amending), ('draft', True))
        self.assertNotEqual(job.state, 'cancel')

    def test_cancel_still_cancels_when_it_is_meant(self):
        order = self._order()
        job = self._jobs(order)
        wizard = self.env['lab.order.cancel.choice'].create({'order_id': order.id})
        wizard.action_cancel_order()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(job.state, 'cancel', "a cancelled order stops its production, as before")

    # ------------------------------------------------------ amended, afterwards
    def test_an_amended_order_is_found_afterwards(self):
        """(client, 2026-11-26: "filter of amended SO")"""
        order, other = self._order(), self._order()
        self.assertFalse(order.lab_amend_count)
        order.action_amend()
        self.assertEqual(order.lab_amend_count, 1, "counted from the moment it is taken back")
        order.action_confirm()
        self.assertEqual(order.lab_amend_count, 1)
        self.assertTrue(order.lab_amended_on)
        self.assertEqual(order.lab_amended_by_id, self.env.user)
        found = self.env['sale.order'].search(
            [('lab_amend_count', '>', 0), ('id', 'in', (order | other).ids)])
        self.assertEqual(found, order)
        self._amend_and_confirm(order)
        self.assertEqual(order.lab_amend_count, 2)

    # ---------------------------------------------- the unit price, when amended
    def test_an_invoiced_line_can_be_repriced_in_an_amendment(self):
        """(client, 2026-11-27: "On amending a SO unit price also to be edited")"""
        order = self._order()
        invoice = order._create_invoices()               # left in draft
        self.assertEqual(order.order_line.qty_invoiced, 1)
        order.action_amend()
        order.order_line.price_unit = 750.0
        order.action_confirm()
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(invoice.invoice_line_ids.price_unit, 750.0, "the draft invoice follows")
        self.assertIn('carried to the draft invoice', ' '.join(order.message_ids.mapped('body')))

    def test_a_posted_invoice_keeps_its_price_and_the_order_says_so(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        order.action_amend()
        order.order_line.price_unit = 750.0
        order.action_confirm()
        self.assertEqual(invoice.invoice_line_ids.price_unit, 1000.0, "the books are not rewritten")
        self.assertIn('already posted', ' '.join(order.message_ids.mapped('body')))

    def test_the_price_is_open_on_the_form_while_amending(self):
        arch = self.env['sale.order'].get_view(self.env.ref('sale.view_order_form').id, 'form')['arch']
        self.assertIn('qty_invoiced &gt; 0 and not parent.lab_amending', arch)


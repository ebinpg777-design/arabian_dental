# -*- coding: utf-8 -*-
"""Closing the jobs the floor and the office have both finished with.
(client, 2026-09-25)

Two rules, run by a scheduler because neither has a moment to hang a hook on:
the last step was scanned some time ago, the invoice aged past its grace some
time ago. What is asserted hardest here is the direction each rule REFUSES,
because both close work in bulk - 1,506 of 3,479 open jobs on the live database
met the second rule on its first run - and a job closed wrongly is work the lab
cannot put back.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCloseFinishedJobs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Close Appliance', 'is_storable': True})
        cls.workcenters = cls.env['mrp.workcenter'].create([
            {'name': 'Close Bench A'}, {'name': 'Close Bench B'}])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [
                (0, 0, {'name': 'Step %s' % i, 'workcenter_id': wc.id})
                for i, wc in enumerate(cls.workcenters, start=1)],
        })
        cls.bare_product = cls.env['product.product'].create({
            'name': 'Close Appliance No Steps', 'is_storable': True})
        cls.Production = cls.env['mrp.production']

    def _job(self, with_steps=True):
        # A product with no BoM of its own: leaving bom_id out is not enough,
        # action_confirm finds a BoM BY PRODUCT and the job gets its steps anyway.
        product = self.product if with_steps else self.bare_product
        mo = self.Production.create(dict(
            {'product_id': product.id, 'product_qty': 1.0},
            **({'bom_id': self.bom.id} if with_steps else {})))
        mo.action_confirm()
        return mo

    def _finish_steps(self, mo, how_many=None):
        steps = mo.workorder_ids
        for step in (steps if how_many is None else steps[:how_many]):
            step.write({'state': 'done'})
        return steps

    # -------------------------------------------------------- every step done
    def test_a_job_whose_every_step_is_done_is_closed(self):
        mo = self._job()
        self._finish_steps(mo)
        self.assertTrue(mo._lab_every_step_done())

    def test_a_job_with_a_step_still_open_is_not(self):
        mo = self._job()
        self._finish_steps(mo, how_many=1)
        self.assertFalse(mo._lab_every_step_done())

    def test_a_job_with_no_steps_at_all_is_not_closed_by_this_rule(self):
        """"All of nothing is done" is how you close an untouched job. It is
        left to the delivery rule, which knows whether it shipped."""
        mo = self._job(with_steps=False)
        self.assertFalse(mo.workorder_ids)
        self.assertFalse(mo._lab_every_step_done())

    def test_an_empty_relation_does_not_pass_for_finished_work(self):
        """THE one that matters. workorder_ids reads blank inside core's
        circular compute, and all() over an empty list is True - so the obvious
        one-liner closes a job with open steps exactly when the bug hiding them
        is biting.

        The blank read is forced here by putting an empty value in the cache,
        which is the shape of the real thing: the rows are in the table, the
        relation says there are none.
        """
        mo = self._job()
        self._finish_steps(mo, how_many=1)
        self.env.flush_all()
        # Odoo 20: the cache is set through the field (env.cache is deprecated)
        mo._fields['workorder_ids']._update_cache(mo, ())
        self.assertFalse(mo.workorder_ids, "the relation now lies, as it does live")

        self.assertFalse(mo._lab_every_step_done(),
                         "a job with an open step must not close on a blank read")
        self.assertEqual(len(mo._lab_step_states()[mo.id]), 2,
                         "the steps were counted in SQL instead")

    def test_the_sql_fallback_sees_the_step_the_relation_hid(self):
        mo = self._job()
        self._finish_steps(mo, how_many=1)
        self.env.flush_all()
        states = mo._lab_step_states()
        self.assertEqual(len(states[mo.id]), 2,
                         "both steps counted, whatever the relation says")
        self.assertIn('done', states[mo.id])
        self.assertFalse(all(st in ('done', 'cancel') for st in states[mo.id]),
                         "the unfinished one is still visible, so the job stays open")

    # ----------------------------------------------------- invoiced and aged
    def _invoiced_job(self, days_old, status='invoiced'):
        clinic = self.env['res.partner'].create({'name': 'Close Clinic'})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        mo = self._job()
        # the lab's mrp.production.sale_id is a stored related on the sale line
        # (sale_custom): writing the order itself writes nothing
        mo.sale_line_id = order.order_line[:1]
        # Through the order, not beside it: sale.order.invoice_ids is computed
        # from the lines' moves, so an invoice created on its own never links.
        order.order_line.write({'qty_delivered': 1})
        invoice = order._create_invoices()
        invoice.action_post()
        self.env.cr.execute(
            "UPDATE account_move SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=days_old), invoice.id))
        invoice.invalidate_recordset(['create_date'])
        # Flush FIRST: invoice_status is compute+store, and the next ORM call
        # flushes the pending recompute straight over this UPDATE.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE sale_order SET invoice_status = %s WHERE id = %s",
            (status, order.id))
        self.env.invalidate_all()
        return mo

    def test_a_fully_invoiced_job_past_the_grace_is_closed(self):
        mo = self._invoiced_job(days_old=5)
        self.assertTrue(mo._lab_invoiced_and_settled(2))

    def test_an_invoice_raised_today_is_left_alone(self):
        """The wait is the point: an invoice raised this morning can still be
        corrected this afternoon, and a job closed under that cannot go back."""
        mo = self._invoiced_job(days_old=0)
        self.assertFalse(mo._lab_invoiced_and_settled(2))

    def test_a_partly_invoiced_order_is_left_alone(self):
        mo = self._invoiced_job(days_old=5, status='to invoice')
        self.assertFalse(mo._lab_invoiced_and_settled(2))

    def test_a_job_with_no_order_is_left_alone(self):
        mo = self._job()
        self.assertFalse(mo._lab_invoiced_and_settled(2))

    # ------------------------------------------------------------- the switch
    def test_the_switch_stops_the_whole_thing(self):
        self.env['ir.config_parameter'].sudo().set_str(
            'lab_workcenter_scan.close_finished_mo', 'False')
        self.assertFalse(self.Production._lab_close_finished_enabled())
        self.assertEqual(self.Production._cron_close_finished_jobs(), 0)

    def test_a_switch_nobody_has_saved_still_runs(self):
        """A key never written reads empty (Odoo 19's get_param gave False, and
        str(False) is 'False'); read naively an untouched setting would mean OFF."""
        params = self.env['ir.config_parameter'].sudo()
        params.search(
            [('key', '=', 'lab_workcenter_scan.close_finished_mo')]).unlink()
        self.assertTrue(self.Production._lab_close_finished_enabled())

    def test_the_settings_have_sane_defaults(self):
        params = self.env['ir.config_parameter'].sudo()
        for key in ('close_invoiced_days', 'close_finished_batch'):
            params.search(
                [('key', '=', 'lab_workcenter_scan.%s' % key)]).unlink()
        self.assertEqual(self.Production._lab_close_finished_settings(), (2, 500))

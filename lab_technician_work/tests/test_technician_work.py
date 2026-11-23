# -*- coding: utf-8 -*-
"""Technician Work (client, 2026-11-23: "a report of technicians' work based on the SO
line, to check the count of work done by a technician")."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestTechnicianWork(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env['res.partner']
        cls.clinic = Partner.create({'name': 'TW Dental Care'})
        cls.anu = Partner.create({'name': 'TW ANU', 'is_technician': True})
        cls.binu = Partner.create({'name': 'TW BINU', 'is_technician': True})
        cls.crown = cls.env['product.product'].create({
            'name': 'TW Crown', 'type': 'consu', 'list_price': 1000.0, 'taxes_id': [(5, 0, 0)]})
        cls.denture = cls.env['product.product'].create({
            'name': 'TW Denture', 'type': 'consu', 'list_price': 3000.0, 'taxes_id': [(5, 0, 0)]})
        # a case worked by both: a crown on three teeth and a denture
        cls.shared = cls._order(cls.anu | cls.binu, [(cls.crown, 3, '11, 12, 13'), (cls.denture, 1, '')])
        # a case worked by ANU alone, a rework
        cls.alone = cls._order(cls.anu, [(cls.crown, 1, '21')], is_rework=True)
        # cancelled: no work
        cls.cancelled = cls._order(cls.binu, [(cls.crown, 1, '31')])
        cls.cancelled.action_cancel()
        cls.Work = cls.env['sale.technician.work']

    @classmethod
    def _order(cls, technicians, lines, **vals):
        return cls.env['sale.order'].create(dict({
            'partner_id': cls.clinic.id,
            'technician_ids': [(6, 0, technicians.ids)],
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty, 'teeth': teeth})
                           for product, qty, teeth in lines],
        }, **vals))

    def _totals(self, technician, domain=()):
        [(cases, reworks, quantity, teeth, amount, share, works)] = self.Work._read_group(
            [('technician_id', '=', technician.id), ('order_id', 'in', (self.shared | self.alone | self.cancelled).ids),
             *domain],
            [], ['case_count:sum', 'rework_count:sum', 'quantity:sum', 'teeth_count:sum', 'amount:sum',
                 'amount_share:sum', '__count'])
        return {'cases': cases, 'reworks': reworks, 'quantity': quantity, 'teeth': teeth,
                'amount': amount, 'share': share, 'works': works}

    def test_every_line_counts_for_every_technician_of_the_case(self):
        anu = self._totals(self.anu)
        self.assertEqual((anu['cases'], anu['works'], anu['reworks']), (2, 3, 1))
        self.assertEqual((anu['quantity'], anu['teeth']), (5.0, 4))
        binu = self._totals(self.binu)
        self.assertEqual((binu['cases'], binu['works'], binu['reworks']), (1, 2, 0),
                         "the cancelled case is no work")
        self.assertEqual((binu['quantity'], binu['teeth']), (4.0, 3))

    def test_the_value_of_a_shared_case_is_divided(self):
        anu, binu = self._totals(self.anu), self._totals(self.binu)
        self.assertEqual(binu['amount'], 6000.0, "the whole value of the case they were on")
        self.assertEqual(binu['share'], 3000.0, "half of it: two technicians")
        self.assertEqual(anu['share'], 3000.0 + 1000.0)
        self.assertEqual(anu['share'] + binu['share'], self.shared.amount_untaxed + self.alone.amount_untaxed)

    def test_reworks_and_shared_cases_can_be_told_apart(self):
        self.assertEqual(self._totals(self.anu, [('is_rework', '=', False)])['works'], 2)
        self.assertEqual(self._totals(self.anu, [('technician_count', '=', 1)])['works'], 1)

    def test_the_technician_opens_their_work(self):
        self.anu.invalidate_recordset(['technician_work_count'])
        self.assertGreaterEqual(self.anu.technician_work_count, 3)
        self.assertEqual(self.clinic.technician_work_count, 0)
        action = self.anu.action_view_technician_work()
        self.assertEqual(action['res_model'], 'sale.technician.work')
        self.assertEqual(self.Work.search(action['domain']).technician_id, self.anu)

    def test_the_summary_prints_each_technicians_work(self):
        today = fields.Date.context_today(self.Work)
        wizard = self.env['sale.technician.work.summary'].create({
            'date_from': today - timedelta(days=1), 'date_to': today,
            'technician_ids': [(6, 0, (self.anu | self.binu).ids)], 'show_products': True})
        rows = {row['technician']: row for row in wizard._rows()}
        self.assertEqual((rows[self.anu]['cases'], rows[self.anu]['works']), (2, 3))
        self.assertEqual({p['product'] for p in rows[self.anu]['products']}, {self.crown, self.denture})
        wizard.include_reworks = False
        rows = {row['technician']: row for row in wizard._rows()}
        self.assertEqual(rows[self.anu]['works'], 2)
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_technician_work.action_report_technician_work', wizard.ids)[0].decode()
        for text in ('TECHNICIAN WORK', 'TW ANU', 'TW BINU', 'TW Crown'):
            self.assertIn(text, html)
        action = wizard.action_open_analysis()
        self.assertEqual(action['res_model'], 'sale.technician.work')

    def test_a_sales_user_reads_it(self):
        clerk = new_test_user(self.env, login='tw_clerk', groups='sales_team.group_sale_salesman')
        self.assertTrue(self.Work.with_user(clerk).search_count([('technician_id', '=', self.anu.id)]))
        self.env['sale.technician.work.summary'].with_user(clerk).create({})._rows()

# -*- coding: utf-8 -*-
"""Technician Work (client, 2026-11-23: "a report of technicians' work based on the SO
line, to check the count of work done by a technician")."""
from datetime import date, timedelta

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


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

    def _options(self, **extra):
        today = fields.Date.context_today(self.Work)
        return dict({'date_from': fields.Date.to_string(today - timedelta(days=1)),
                     'date_to': fields.Date.to_string(today),
                     'technician_ids': (self.anu | self.binu).ids}, **extra)

    def test_the_technician_opens_their_work(self):
        self.anu.invalidate_recordset(['technician_work_count'])
        self.assertGreaterEqual(self.anu.technician_work_count, 3)
        self.assertEqual(self.clinic.technician_work_count, 0)
        action = self.anu.action_view_technician_work()
        self.assertEqual((action['type'], action['tag']), ('ir.actions.client', 'lab_technician_work_board'))
        self.assertEqual(action['context']['technician_ids'], self.anu.ids)

    def test_the_board_shows_each_technicians_work_and_the_period_before(self):
        board = self.Work.get_board(self._options())
        rows = {row['key']: row for row in board['rows']}
        anu, binu = rows[self.anu.id], rows[self.binu.id]
        self.assertEqual((anu['cases'], anu['works'], anu['reworks']), (2, 3, 1))
        self.assertEqual((anu['rank'], binu['rank']), (1, 2), "the busiest first")
        self.assertEqual(anu['rework_rate'], 50.0)
        self.assertEqual(anu['works_pct'], 60.0, "3 of the 5 works")
        self.assertEqual(anu['trend'], 3, "nothing the period before")
        self.assertEqual(sum(anu['series']), 3, "the daily series adds up to the works")
        self.assertEqual(anu['active_days'], 1)
        self.assertEqual(board['totals']['people'], 2)
        self.assertEqual(board['totals']['works'], 5)
        self.assertEqual(board['period']['unit'], 'day')
        self.assertTrue(board['insights'])
        self.assertEqual({c['id'] for c in board['choices']['technician_ids']}, {self.anu.id, self.binu.id})

    def test_the_board_filters_and_groups(self):
        no_rework = self.Work.get_board(self._options(rework='exclude'))
        self.assertEqual({r['key']: r['works'] for r in no_rework['rows']}[self.anu.id], 2)
        alone = self.Work.get_board(self._options(cases='alone'))
        self.assertEqual([r['key'] for r in alone['rows']], self.anu.ids)
        by_product = self.Work.get_board(self._options(group_by='product_id'))
        products = {r['key']: r['works'] for r in by_product['rows']}
        self.assertEqual(products, {self.crown.id: 3, self.denture.id: 2}, "a line once per technician")
        only_binu = self.Work.get_board(self._options(technician_ids=self.binu.ids))
        self.assertEqual([r['key'] for r in only_binu['rows']], self.binu.ids)
        self.assertEqual({c['id'] for c in only_binu['choices']['technician_ids']}, {self.anu.id, self.binu.id},
                         "a filter still offers what it leaves out")

    def test_a_period_is_compared_with_the_like_period_before(self):
        previous = self.Work._previous_period
        self.assertEqual(previous(date(2026, 11, 1), date(2026, 11, 23)), (date(2026, 10, 1), date(2026, 10, 23)),
                         "this month so far: the same days last month")
        self.assertEqual(previous(date(2026, 3, 1), date(2026, 3, 31)), (date(2026, 2, 1), date(2026, 2, 28)),
                         "a whole month: the whole month before")
        self.assertEqual(previous(date(2026, 1, 1), date(2026, 3, 31)), (date(2025, 10, 1), date(2025, 12, 31)))
        self.assertEqual(previous(date(2026, 11, 10), date(2026, 11, 16)), (date(2026, 11, 3), date(2026, 11, 9)))

    def test_a_row_opens_into_its_products_and_its_lines(self):
        breakdown = self.Work.get_breakdown(self._options(), self.anu.id, 'product_id')
        self.assertEqual({r['key']: r['works'] for r in breakdown['rows']}, {self.crown.id: 2, self.denture.id: 1})
        action = self.Work.action_open_lines(self._options(), self.anu.id)
        self.assertEqual(self.Work.search(action['domain']).technician_id, self.anu)

    def test_the_board_prints_and_exports_as_filtered(self):
        self.Work.action_print(self._options(), ['works', 'cases', 'teeth', 'trend'], True)
        summary = self.env['sale.technician.work.summary'].search([], order='id desc', limit=1)
        self.assertEqual(summary.columns, ['works', 'cases', 'teeth', 'trend'])
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_technician_work.action_report_technician_work', summary.ids)[0].decode()
        for text in ('TECHNICIAN WORK', 'TW ANU', 'TW BINU', 'TW Crown', 'vs Previous'):
            self.assertIn(text, html)
        export = self.Work.action_export(self._options(), ['works', 'cases'])
        self.assertEqual(export['type'], 'ir.actions.act_url')
        attachment = self.env['ir.attachment'].browse(int(export['url'].split('/')[3].split('?')[0]))
        self.assertEqual(bytes(attachment.raw)[:2], b'PK', "an xlsx is a zip")

    def test_a_sales_user_reads_it(self):
        clerk = new_test_user(self.env, login='tw_clerk', groups='sales_team.group_sale_salesman')
        self.assertTrue(self.Work.with_user(clerk).search_count([('technician_id', '=', self.anu.id)]))
        board = self.Work.with_user(clerk).get_board(self._options())
        self.assertTrue(board['rows'])


@tagged('post_install', '-at_install')
class TestTechnicianBoardScreen(HttpCase):
    """The screen drawn in a browser: the data tests prove the figures, not the template."""

    def test_the_screen_draws_its_tiles_rows_and_heatmap(self):
        clinic = self.env['res.partner'].create({'name': 'TWS Clinic'})
        tech = self.env['res.partner'].create({'name': 'TWS TECH', 'is_technician': True})
        product = self.env['product.product'].create({'name': 'TWS Crown', 'type': 'consu', 'list_price': 100.0})
        self.env['sale.order'].create({
            'partner_id': clinic.id, 'technician_ids': [(6, 0, tech.ids)],
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 2, 'teeth': '11, 12'})]})
        code = """
            const wait = (sel) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = document.querySelector(sel);
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 150) { clearInterval(t); reject(new Error('not drawn: ' + sel)); }
                }, 100);
            });
            (async () => {
                await wait('.o_tw_tile');
                await wait('.o_tw_row');
                if (!document.body.textContent.includes('TWS TECH')) { throw new Error('the technician is missing'); }
                document.querySelector('.o_tw_row').click();
                await wait('.o_tw_break_row');
                [...document.querySelectorAll('.o_tw_tools .o_tw_seg button')][1].click();
                await wait('.o_tw_heat_cell');
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """
        self.browser_js('/odoo/action-lab_technician_work.action_technician_board', code, login='admin', timeout=90)

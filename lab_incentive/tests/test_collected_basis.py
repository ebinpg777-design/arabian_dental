# -*- coding: utf-8 -*-
"""The Collected basis (client, 2026-11-23): "the uncleared PDCs should not be considered
as payments. Also the payments in previous months whose PDC is cleared in this month are
to be considered as payments of this month. The incentive report of each sales person
should contain all the details of payment and PDCs without any confusion."

Months are counted back from today, so every date stays in the past: a cheque cannot be
deposited or cleared on a day still to come.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from .test_ph2_epic_g import IncentiveCommon


class CollectedCommon(IncentiveCommon):
    """The Collected fixture: a bank with its PDC account, a second salesperson, and
    the months counted back from today. Holds no tests of its own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params.set_str('lab_incentive.basis', 'collected')
        company = cls.env.company
        Account = cls.env['account.account']

        def account(code, name, atype, reconcile=False):
            return Account.search([('code', '=', code), ('company_ids', 'in', company.id)],
                                  limit=1) or Account.create({
                'code': code, 'name': name, 'account_type': atype,
                'reconcile': reconcile, 'company_ids': [(4, company.id)]})

        cls.bank = cls.env['account.journal'].create({
            'name': 'Incentive Test Bank', 'code': 'ITBK', 'type': 'bank',
            'company_id': company.id,
            'default_account_id': account('ITB100', 'Incentive Test Bank', 'asset_cash').id})
        cls.bank.inbound_payment_method_line_ids.payment_account_id = account(
            'ITB101', 'Incentive Test Outstanding', 'asset_current', True)
        cls.bank.lab_pdc_account_id = account('ITB102', 'PDC - Incentive Test Bank',
                                              'asset_current', True)
        # an internal salesperson: the generator leaves out share users
        cls.seller = cls.env['res.users'].create({
            'name': 'Collected Seller', 'login': 'inc_collected_seller',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id,
                                  cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        today = fields.Date.context_today(cls.env['lab.incentive.sheet'])
        # this month, last month (the month under test), the month before it
        cls.this_month = today.replace(day=1)
        cls.month = cls.this_month - relativedelta(months=1)
        cls.before = cls.month - relativedelta(months=1)

    # ------------------------------------------------------------------ helpers
    def _invoice(self, product=None, qty=1, user=None):
        return self._order(product or self.plain, qty=qty, user=user).invoice_ids

    def _cheque(self, invoice, amount, received, deposit=None, clear=None, number=None):
        cheque = self.env['lab.cheque'].create({
            'cheque_number': number or 'IC-%s-%s' % (invoice.id, received),
            'partner_id': invoice.partner_id.id, 'amount': amount,
            'cheque_date': received, 'received_date': received,
            'journal_id': self.bank.id, 'bank_name': 'Federal Bank',
            'allocation_ids': [(0, 0, {'move_id': invoice.id, 'amount': amount})]})
        cheque.action_confirm_receipt()
        if deposit:
            cheque.deposit_date = deposit
            cheque.action_deposit()
        if clear:
            cheque.clear_date = clear
            cheque.action_clear()
        return cheque

    def _cash(self, invoice, amount, day):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': invoice.partner_id.id, 'amount': amount, 'date': day,
            'journal_id': self.bank.id})
        payment.action_post()
        _liquidity, counterpart, _writeoff = payment._seek_for_lines()
        receivable = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        (counterpart | receivable).reconcile()
        return payment

    def _computed(self, period, executive=None):
        sheet = self._sheet(executive=executive, period=period)
        sheet.action_compute()
        return sheet



@tagged('post_install', '-at_install')
class TestCollectedBasis(CollectedCommon):

    # ------------------------------------------------------------------ cheques
    def test_a_cheque_not_cleared_is_not_a_payment(self):
        invoice = self._invoice(qty=3)
        cheque = self._cheque(invoice, 3000.0, received=self.month + relativedelta(days=4),
                              deposit=self.month + relativedelta(days=6))
        self.assertTrue(cheque.receipt_booked, "fixture: the receipt settled the invoice")
        sheet = self._computed(self.month)
        self.assertEqual(sheet.basis, 'collected')
        self.assertFalse(sheet.line_ids, "nothing counts while the bank has not cleared it")
        self.assertEqual(sheet.total_sales, 0.0)
        row = sheet.collection_ids
        self.assertEqual((row.status, row.cheque_id, row.counted_amount), ('pending', cheque, 0.0))
        self.assertEqual(sheet.cheques_waiting_count, 1)
        self.assertEqual(sheet.cheques_waiting_amount, 3000.0)
        self.assertIn('not cleared', row.note)

    def test_a_cheque_counts_in_the_month_it_clears_though_received_earlier(self):
        invoice = self._invoice(qty=2)
        cheque = self._cheque(invoice, 2000.0, received=self.before + relativedelta(days=20),
                              deposit=self.before + relativedelta(days=25),
                              clear=self.month + relativedelta(days=2))
        sheet = self._computed(self.month)
        row = sheet.collection_ids
        self.assertEqual(row.status, 'counted')
        self.assertTrue(row.from_earlier_month)
        self.assertEqual(row.counted_date, cheque.clear_date)
        self.assertEqual(sheet.total_sales, 2000.0)
        self.assertEqual(sheet.collected_cheques, 2000.0)
        self.assertEqual(sheet.collected_earlier, 2000.0)
        self.assertEqual(sheet.line_ids.counted_date, cheque.clear_date)
        self.assertEqual(sheet.line_ids.receipt_ref, cheque.payment_id.name)
        self.assertEqual(sheet.incentive_amount, 100.0, "5% on 2,000")

        # the month it came in lists it as clearing later, and counts nothing
        earlier = self._computed(self.before)
        self.assertEqual(earlier.collection_ids.status, 'later')
        self.assertEqual(earlier.total_sales, 0.0)
        self.assertIn('counted in', earlier.collection_ids.note)

    def test_cash_and_bank_count_on_their_own_date(self):
        invoice = self._invoice(qty=4)
        payment = self._cash(invoice, 1500.0, self.month + relativedelta(days=10))
        sheet = self._computed(self.month)
        row = sheet.collection_ids
        self.assertEqual((row.status, row.mode, row.payment_id), ('counted', 'bank', payment))
        self.assertEqual(sheet.total_sales, 1500.0, "part of the bill paid, that part counts")
        self.assertEqual(sheet.collected_cash_bank, 1500.0)
        self.assertFalse(self._computed(self.before).collection_ids)

    def test_only_the_executives_lines_count_and_before_tax(self):
        tax = self.env['account.tax'].create({
            'name': 'Incentive Test 10%', 'amount': 10.0, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': self.env.company.id})
        taxed = self.env['product.product'].create({
            'name': 'Taxed Appliance', 'type': 'consu', 'list_price': 1000.0,
            'invoice_policy': 'order', 'taxes_id': [(6, 0, tax.ids)]})
        mine = self._order(taxed, qty=1, bill=False)
        theirs = self._order(taxed, qty=3, user=self.seller, bill=False)
        invoice = (mine | theirs)._create_invoices()
        invoice.action_post()
        self.assertEqual(len(invoice), 1, "fixture: one invoice for both orders")
        self.assertEqual(invoice.amount_total, 4400.0)
        self._cash(invoice, 2200.0, self.month + relativedelta(days=3))   # half of it
        sheet = self._computed(self.month)
        self.assertEqual(sheet.collection_ids.amount, 2200.0)
        self.assertEqual(sheet.total_sales, 500.0, "half of my 1,000, without the tax")
        other = self._computed(self.month, executive=self.seller)
        self.assertEqual(other.total_sales, 1500.0)

    def test_a_cheque_bounced_before_clearing_is_listed_never_counted(self):
        invoice = self._invoice()
        cheque = self._cheque(invoice, 1000.0, received=self.this_month,
                              deposit=self.this_month)
        cheque.bounce_reason = 'Insufficient funds'
        cheque.action_bounce()
        sheet = self._computed(self.this_month)
        row = sheet.collection_ids
        self.assertEqual((row.status, row.counted_amount), ('bounced', 0.0))
        self.assertEqual(sheet.cheques_bounced_count, 1)
        self.assertFalse(sheet.line_ids)

    def test_a_cheque_counted_last_month_that_bounces_is_taken_back(self):
        invoice = self._invoice(qty=2)
        cheque = self._cheque(invoice, 2000.0, received=self.month + relativedelta(days=1),
                              deposit=self.month + relativedelta(days=2),
                              clear=self.month + relativedelta(days=4))
        cheque.bounce_reason = 'Returned by the bank'
        cheque.action_bounce()
        now = self._computed(self.this_month)
        row = now.collection_ids
        self.assertEqual(row.status, 'taken_back')
        self.assertEqual(now.total_sales, -2000.0)
        self.assertEqual(now.taken_back_amount, 2000.0)
        # the month it cleared still counts it: that is where it is taken back from
        then = self._computed(self.month)
        self.assertEqual(then.collection_ids.status, 'counted')
        self.assertEqual(then.total_sales, 2000.0)

    def test_the_paid_basis_does_not_count_an_uncleared_cheque_either(self):
        self.params.set_str('lab_incentive.basis', 'paid')
        invoice = self._invoice()
        today = fields.Date.context_today(self.env['lab.incentive.sheet'])
        cheque = self._cheque(invoice, 1000.0, received=today, deposit=today)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.line_ids, "settled by a cheque the bank has not cleared")
        cheque.clear_date = today
        cheque.action_clear()
        sheet.action_compute()
        self.assertEqual(sheet.total_sales, 1000.0)

    # ------------------------------------------------------------------ screens
    def test_the_widget_shows_the_month_and_each_cheques_journey(self):
        invoice = self._invoice(qty=2)
        self._cheque(invoice, 2000.0, received=self.before + relativedelta(days=20),
                     deposit=self.before + relativedelta(days=25),
                     clear=self.month + relativedelta(days=2), number='JOURNEY1')
        data = self._computed(self.month).collection_widget_data
        self.assertEqual([t['key'] for t in data['tiles']],
                         ['total', 'cash', 'cleared', 'waiting', 'bounced'])
        journey = data['cheques'][0]
        self.assertEqual(journey['number'], 'JOURNEY1')
        kinds = {e['kind']: e for e in journey['events']}
        self.assertTrue(kinds['received']['before'], "received before the month: at its start")
        self.assertEqual(kinds['received']['pos'], 0.0)
        self.assertGreater(kinds['cleared']['pos'], 0.0)

    def test_month_end_previews_and_generates_for_whoever_collected(self):
        invoice = self._invoice(user=self.seller, qty=3)    # billed this month
        self._cash(invoice, 3000.0, self.month + relativedelta(days=5))
        wizard = self.env['lab.incentive.generate.wizard'].create(
            {'period': self.month, 'basis': 'collected'})
        rows = {r['id']: r for r in wizard.preview_data['rows']}
        self.assertIn(self.seller.id, rows, "collected in the month, billed in another")
        self.assertEqual(rows[self.seller.id]['cash'], 3000.0)
        self.assertEqual(rows[self.seller.id]['incentive'], 150.0)
        action = wizard.action_generate()
        sheet = self.env['lab.incentive.sheet'].search(
            action['domain'] + [('executive_id', '=', self.seller.id)])
        self.assertEqual(sheet.total_sales, 3000.0)
        self.assertEqual(sheet.incentive_amount, rows[self.seller.id]['incentive'],
                         "the preview is what the sheet says")

    def test_choosing_a_basis_in_the_wizard_saves_it(self):
        wizard = self.env['lab.incentive.generate.wizard'].create(
            {'period': self.month, 'basis': 'invoiced'})
        self.assertEqual(wizard.preview_data['basis'], 'invoiced')
        wizard.action_generate()
        self.assertEqual(self.env['lab.incentive.sheet']._incentive_basis(), 'invoiced')

    def test_the_statement_lists_every_receipt_and_cheque(self):
        invoice = self._invoice(qty=2)
        self._cheque(invoice, 1000.0, received=self.before + relativedelta(days=20),
                     deposit=self.before + relativedelta(days=21),
                     clear=self.month + relativedelta(days=1), number='PDF-CLEARED')
        self._cheque(invoice, 500.0, received=self.month + relativedelta(days=9),
                     number='PDF-WAITING')
        sheet = self._computed(self.month)
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_incentive.action_report_incentive_sheet', sheet.ids)[0].decode()
        for text in ('Receipts counted this month', 'PDF-CLEARED',
                     'Cheques not counted this month', 'PDF-WAITING', 'not cleared'):
            self.assertIn(text, html)

    def test_an_executive_reads_the_receipts_of_their_own_sheet(self):
        invoice = self._invoice()
        self._cash(invoice, 1000.0, self.month + relativedelta(days=3))
        sheet = self._computed(self.month)
        self.env.invalidate_all()
        data = sheet.with_user(self.executive).web_read({
            'collection_widget_data': {}, 'total_collected': {},
            'collection_ids': {'fields': {'status': {}, 'receipt_ref': {}, 'counted_amount': {},
                                          'partner_id': {'fields': {'display_name': {}}}}}})[0]
        self.assertEqual(len(data['collection_ids']), 1)
        self.assertEqual(data['total_collected'], 1000.0)
        self.assertTrue(data['collection_widget_data']['tiles'])


@tagged('post_install', '-at_install')
class TestCollectedScreens(CollectedCommon, HttpCase):
    """The widgets drawn in a browser: the data tests above prove the figures, not that
    the templates render them."""

    WAIT = """
        const wait = (sel) => new Promise((resolve, reject) => {
            let n = 0;
            const t = setInterval(() => {
                if (document.querySelector(sel)) { clearInterval(t); resolve(); }
                else if (++n > 150) { clearInterval(t); reject(new Error('not drawn: ' + sel)); }
            }, 100);
        });
    """

    def test_the_sheet_draws_the_months_money_and_the_cheques(self):
        invoice = self._invoice(qty=2)
        self._cheque(invoice, 1000.0, received=self.before + relativedelta(days=20),
                     deposit=self.before + relativedelta(days=21),
                     clear=self.month + relativedelta(days=1), number='UI-CLEARED')
        self._cheque(invoice, 500.0, received=self.month + relativedelta(days=9),
                     number='UI-WAITING')
        self._cash(invoice, 300.0, self.month + relativedelta(days=12))
        sheet = self._computed(self.month)
        code = self.WAIT + """
            (async () => {
                await wait('.o_inc_tile');
                await wait('.o_inc_row.o_inc_st_counted .o_inc_dot.o_inc_ev_cleared');
                await wait('.o_inc_row.o_inc_st_pending .o_inc_track');
                if (document.querySelectorAll('.o_inc_tile').length !== 5) {
                    throw new Error('five tiles expected');
                }
                if (!document.body.textContent.includes('UI-WAITING')) {
                    throw new Error('the waiting cheque is not listed');
                }
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """
        self.browser_js('/odoo/action-lab_incentive.action_incentive_sheet/%s' % sheet.id,
                        code, login='admin', timeout=90)

    def test_the_wizard_previews_month_end(self):
        # the wizard opens on the current month
        invoice = self._invoice(user=self.seller, qty=3)
        self._cash(invoice, 3000.0, self.this_month)
        code = self.WAIT + """
            (async () => {
                await wait('.o_inc_preview_table tbody tr');
                await wait('.o_inc_minibar .o_inc_mb_cash');
                if (!document.querySelector('.o_inc_preview_table').textContent.includes('Collected Seller')) {
                    throw new Error('the seller is not previewed');
                }
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """
        self.browser_js('/odoo/action-lab_incentive.action_generate_sheets', code,
                        login='admin', timeout=90)

# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta

import json
from urllib.parse import unquote

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase


@tagged('post_install', '-at_install')
class TestPartnerStatement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Statement Clinic', 'is_company': True, 'email': 'clinic@example.com',
            # No payment term, deliberately. These tests age documents by their own
            # dates, and every partner on this database now defaults to "30 Days"
            # (client, 2026-08-19) — which silently moved a 45-day-old invoice from
            # the 31-60 bucket to 1-30 and made the ageing assertions test the default
            # rather than the ageing. (client, 2026-08-27)
            'property_payment_term_id': False})
        cls.product = cls.env['product.product'].create({'name': 'Appliance', 'type': 'service', 'list_price': 100})
        cls.Engine = cls.env['epg.partner.statement']
        cls.today = fields.Date.today()

    def _invoice(self, amount, inv_date, post=True, partner=None):
        inv = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': (partner or self.partner).id, 'invoice_date': inv_date,
            'invoice_date_due': inv_date, 'date': inv_date,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': amount,
                                         'tax_ids': [(6, 0, [])]})],
        })
        if post:
            inv.action_post()
        return inv

    def _pay(self, invoice, amount, pay_date):
        return self._pay_partner(self.partner, invoice, amount, pay_date)

    def _pay_partner(self, partner, invoice, amount, pay_date):
        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.company.id)], limit=1)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': partner.id,
            'amount': amount, 'date': pay_date, 'journal_id': journal.id,
        })
        payment.action_post()
        lines = (payment.move_id.line_ids + invoice.line_ids).filtered(
            lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
        lines.reconcile()
        return payment

    def _options(self, date_from, date_to, **kw):
        opts = {'statement_type': 'receivable', 'date_from': date_from, 'date_to': date_to,
                'company': self.company, 'open_items_only': False, 'show_ageing': True}
        opts.update(kw)
        return opts

    def test_ledger_opening_running_closing(self):
        old = self._invoice(100, self.today - timedelta(days=60))
        new = self._invoice(250, self.today - timedelta(days=5))
        self._pay(new, 50, self.today - timedelta(days=2))
        st = self.Engine.compute(self.partner, self._options(self.today - timedelta(days=30), self.today))[self.partner.id]
        self.assertTrue(st['has_data'])
        block = st['blocks'][0]
        self.assertEqual(block['opening'], 100.0, "invoice before the period is the opening balance")
        self.assertEqual([round(l['balance'], 2) for l in block['lines']], [350.0, 300.0])
        self.assertEqual(block['closing'], 300.0)
        self.assertEqual(block['total_debit'], 250.0)
        self.assertEqual(block['total_credit'], 50.0)

    def test_open_items_and_ageing(self):
        old = self._invoice(100, self.today - timedelta(days=45))     # 45 days overdue -> 31-60
        new = self._invoice(200, self.today - timedelta(days=5))      # 5 days -> 1-30
        paid = self._invoice(70, self.today - timedelta(days=10)); self._pay(paid, 70, self.today - timedelta(days=9))
        st = self.Engine.compute(self.partner, self._options(self.today - timedelta(days=30), self.today, open_items_only=True))[self.partner.id]
        block = st['blocks'][0]
        self.assertEqual({l['move'] for l in block['lines']}, {old.name, new.name}, "only unpaid documents, whatever their date")
        ageing = {r[0]: r[1] for r in block['ageing_rows']}
        self.assertEqual(ageing['31-60 days'], 100.0)
        self.assertEqual(ageing['1-30 days'], 200.0)
        self.assertEqual(block['ageing_total'], 300.0)
        overdue = {l['move']: l['days_overdue'] for l in block['lines']}
        self.assertEqual(overdue[old.name], 45)

    def test_payable_sign_and_type_filter(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id, 'invoice_date': self.today, 'date': self.today,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 40, 'tax_ids': [(6, 0, [])]})]})
        bill.action_post()
        self._invoice(100, self.today)
        recv = self.Engine.compute(self.partner, self._options(self.today, self.today))[self.partner.id]['blocks'][0]
        pay = self.Engine.compute(self.partner, self._options(self.today, self.today, statement_type='payable'))[self.partner.id]['blocks'][0]
        self.assertEqual(recv['closing'], 100.0)
        self.assertEqual(pay['closing'], 40.0, "payables are shown as a positive amount owed to the vendor")

    def test_pdf_and_xlsx_render(self):
        self._invoice(120, self.today)
        opts = self._options(self.today, self.today)
        pdf = self.Engine.render_pdf(self.partner, opts)
        # in test mode Odoo renders the HTML body instead of calling wkhtmltopdf
        self.assertTrue(pdf.startswith(b'%PDF') or self.partner.name.encode() in pdf)
        xlsx = self.Engine.render_xlsx(self.partner, opts)
        self.assertTrue(xlsx.startswith(b'PK'), "xlsx is a zip container")

    def test_wizard_all_with_balance_and_email(self):
        self._invoice(90, self.today)
        other = self.env['res.partner'].create({'name': 'Settled Clinic'})
        settled = self._invoice(10, self.today, partner=other)
        self._pay_partner(other, settled, 10, self.today)
        wiz = self.env['epg.partner.statement.wizard'].create({
            'all_with_balance': True, 'statement_type': 'receivable', 'period': 'custom',
            'date_from': self.today, 'date_to': self.today, 'company_id': self.company.id})
        self.assertIn(self.partner, wiz._partners())
        self.assertNotIn(other, wiz._partners(), "a fully paid partner is not 'with a balance'")
        mails_before = self.env['mail.mail'].search_count([])
        wiz.write({'all_with_balance': False, 'partner_ids': [(6, 0, self.partner.ids)]})
        wiz.action_send_email()
        self.assertGreater(self.env['mail.mail'].search_count([]), mails_before)
        self.assertEqual(self.partner.statement_last_sent, self.today)
        att = self.env['ir.attachment'].search([('res_model', '=', 'res.partner'), ('res_id', '=', self.partner.id), ('mimetype', '=', 'application/pdf')])
        self.assertTrue(att, "the sent statement is kept on the partner")

    def test_period_presets(self):
        d1, d2 = self.Engine.period_dates('last_month', self.company, date(2026, 3, 15))
        self.assertEqual((d1, d2), (date(2026, 2, 1), date(2026, 2, 28)))
        d1, d2 = self.Engine.period_dates('this_quarter', self.company, date(2026, 5, 10))
        self.assertEqual((d1, d2), (date(2026, 4, 1), date(2026, 6, 30)))

    def test_cron_sends_only_opted_in(self):
        self._invoice(55, self.today.replace(day=1) - timedelta(days=3))
        self.partner.write({'statement_auto_send': True})
        self.env['ir.config_parameter'].sudo().set_param('epg_partner_statement.auto_send_day', str(self.today.day))
        sent = self.Engine._cron_auto_send()
        self.assertEqual(sent, 1)
        self.assertEqual(self.Engine._cron_auto_send(), 0, "not sent twice in the same month")

    def test_the_description_names_what_the_invoice_was_for(self):
        """A clinic reconciling a statement recognises the appliance, not the sales
        order number this column used to show. (client, 2026-08-27)"""
        invoice = self._invoice(100, self.today)
        block = self.Engine.compute(
            self.partner, self._options(self.today, self.today)
        )[self.partner.id]['blocks'][0]
        line = next(l for l in block['lines'] if l['move'] == invoice.name)
        if 'product_names' in self.env['account.move']._fields:
            self.assertEqual(line['name'], invoice.product_names)
            self.assertIn(self.product.name, line['name'])

    def test_a_line_with_no_items_still_says_something(self):
        """Opening balances, payments and journal entries name no product, and the
        column must not go blank for them."""
        payment_only = self._invoice(70, self.today)
        self._pay(payment_only, 70, self.today)
        block = self.Engine.compute(
            self.partner, self._options(self.today, self.today)
        )[self.partner.id]['blocks'][0]
        for line in block['lines']:
            self.assertIsInstance(line['name'], str)

    def test_the_period_follows_the_document_date_not_the_entry_date(self):
        """A migrated opening item carries the date it had in the old system, and the
        statement always SHOWED that while FILTERING on the accounting date — so a line
        printed as 2023 was included or excluded by its 2026 migration date. 123,446 of
        this ledger's items are in exactly that position. (client, 2026-08-27)"""
        from datetime import date
        invoice = self._invoice(100, self.today)
        line = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        old_day = date(2023, 10, 3)
        line.original_date = old_day
        line.flush_recordset()
        self.assertEqual(line.statement_date, old_day,
                         "the document's own date is what the statement works on")

        # It now belongs to the period it reads as...
        block = self.Engine.compute(
            self.partner, self._options(date(2023, 1, 1), date(2023, 12, 31))
        )[self.partner.id]['blocks'][0]
        self.assertIn(invoice.name, {l['move'] for l in block['lines']})

        # ...and is history by the time the entry date comes round.
        later = self.Engine.compute(
            self.partner, self._options(self.today, self.today)
        )[self.partner.id]['blocks'][0]
        self.assertNotIn(invoice.name, {l['move'] for l in later['lines']},
                         "it is opening balance now, not movement in this period")

    def test_a_line_with_no_document_date_still_uses_its_entry_date(self):
        invoice = self._invoice(50, self.today)
        line = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertFalse(line.original_date)
        self.assertEqual(line.statement_date, line.date)

    def test_the_statement_still_balances_on_the_new_date_basis(self):
        """Opening + charges - payments must equal closing however the lines are dated;
        filtering on one date and cutting the opening balance on another would list a
        line in the period and count it into the opening balance as well."""
        from datetime import date, timedelta
        old = self._invoice(100, self.today - timedelta(days=200))
        old.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
        ).original_date = date(2023, 5, 1)
        self._invoice(200, self.today)
        block = self.Engine.compute(
            self.partner, self._options(self.today - timedelta(days=30), self.today)
        )[self.partner.id]['blocks'][0]
        self.assertAlmostEqual(
            block['opening'] + block['total_debit'] - block['total_credit'],
            block['closing'], places=2)


@tagged('post_install', '-at_install')
class TestStatementDownloadName(HttpCase):
    """The downloaded PDF must say whose statement it is.

    Odoo names a report download from `print_report_name` only when the URL
    carries the record ids; the wizard passes its partners inside `data` so the
    period travels with them, so that branch never runs and every statement
    arrived called "Statement of Account.pdf" - useless in a phone's Downloads
    folder. (client, 2026-09-01)
    """

    def setUp(self):
        super().setUp()
        self.clinic = self.env['res.partner'].create(
            {'name': 'DR NAME / EKM', 'is_company': True})

    def _download_name(self, partner_ids, date_from='2026-04-01',
                       date_to='2026-09-01'):
        """The Content-Disposition the real /report/download hands back."""
        url = ('/report/pdf/epg_partner_statement.report_statement'
               '?options=%s&context=%%7B%%7D' % json.dumps({
                   'ids': partner_ids, 'statement_type': 'receivable',
                   'date_from': date_from, 'date_to': date_to,
                   'company_id': self.env.company.id,
                   'open_items_only': True, 'show_ageing': False}))
        self.authenticate('admin', 'admin')
        response = self.url_open('/report/download', data={
            'data': json.dumps([url, 'qweb-pdf']),
            'context': '{}',
            'csrf_token': http.Request.csrf_token(self),
        })
        # RFC 5987: the header carries filename*=UTF-8''<percent-encoded>.
        raw = response.headers.get('Content-Disposition', '')
        return unquote(raw)

    def test_one_clinic_is_named_in_the_file(self):
        disposition = self._download_name([self.clinic.id])
        self.assertIn('Statement', disposition)
        # The clinic, and the period the reader chose, both survive.
        self.assertIn('DR NAME', disposition)
        self.assertIn('2026-04-01', disposition)
        self.assertNotIn('Statement of Account.pdf', disposition,
                         "the generic fallback name is the bug")

    def test_the_file_keeps_its_pdf_extension(self):
        """Without it a phone has nothing to pick a viewer from."""
        self.assertIn('.pdf', self._download_name([self.clinic.id]))

    def test_a_slash_in_the_clinic_name_cannot_become_a_path(self):
        disposition = self._download_name([self.clinic.id])
        name = disposition.split('filename', 1)[-1]
        self.assertNotIn('/', name.replace('filename*=UTF-8', ''),
                         "a clinic called 'DR X / EKM' must not invent a folder")

    def test_a_run_of_many_clinics_says_how_many(self):
        other = self.env['res.partner'].create({'name': 'Second Clinic'})
        disposition = self._download_name([self.clinic.id, other.id])
        self.assertIn('2', disposition)
        self.assertNotIn('DR NAME', disposition,
                         "a multi-clinic run belongs to no single clinic")


@tagged('post_install', '-at_install')
class TestEnteredBetweenFilter(TransactionCase):
    """Filtering on when the ENTRY was typed, not what it is dated.

    On this ledger a migrated document carries its Odoo 10 date while its
    create_date is the moment of the import, years later — so "the statement
    for August" and "only what we have entered since the import" are two
    different questions and the wizard now asks both. (client, 2026-09-02)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Engine = cls.env['epg.partner.statement']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Entered Clinic', 'is_company': True,
            'property_payment_term_id': False})
        cls.product = cls.env['product.product'].create(
            {'name': 'Entered Appliance', 'type': 'service', 'list_price': 100})
        cls.day = date(2026, 6, 15)

    def _invoice(self, amount, created_at=None):
        """An invoice, optionally back-stamped as if it had been imported.

        create_date is not writable through the ORM, so the stamp is set in
        SQL — which is exactly what a migration does to these rows.
        """
        inv = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': self.day, 'invoice_date_due': self.day,
            'date': self.day,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id, 'quantity': 1,
                'price_unit': amount, 'tax_ids': [(6, 0, [])]})]})
        inv.action_post()
        if created_at:
            self.env.cr.execute(
                "UPDATE account_move SET create_date = %s WHERE id = %s",
                (created_at, inv.id))
            inv.invalidate_recordset(['create_date'])
        return inv

    def _options(self, **kw):
        opts = {'statement_type': 'receivable',
                'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31),
                'company': self.company, 'open_items_only': False,
                'show_ageing': False}
        opts.update(kw)
        return opts

    def _lines(self, **kw):
        data = self.Engine.compute(self.partner, self._options(**kw))
        blocks = data[self.partner.id]['blocks']
        return [row for block in blocks for row in block['lines']]

    # ------------------------------------------------------------ the filter
    def test_without_the_filter_both_entries_are_listed(self):
        self._invoice(100.0, created_at='2020-01-01 08:00:00')
        self._invoice(200.0)
        self.assertEqual(len(self._lines()), 2)

    def test_entered_after_leaves_the_import_out(self):
        old = self._invoice(100.0, created_at='2020-01-01 08:00:00')
        fresh = self._invoice(200.0)
        rows = self._lines(created_from=datetime(2024, 1, 1))
        names = {r['move'] for r in rows}
        self.assertIn(fresh.name, names)
        self.assertNotIn(old.name, names,
                         "an entry typed before the bound is out of scope")

    def test_entered_before_keeps_only_the_import(self):
        old = self._invoice(100.0, created_at='2020-01-01 08:00:00')
        fresh = self._invoice(200.0)
        rows = self._lines(created_to=datetime(2024, 1, 1))
        names = {r['move'] for r in rows}
        self.assertIn(old.name, names)
        self.assertNotIn(fresh.name, names)

    def test_a_window_takes_both_bounds_together(self):
        self._invoice(100.0, created_at='2020-01-01 08:00:00')
        mid = self._invoice(150.0, created_at='2022-06-01 08:00:00')
        self._invoice(200.0)
        rows = self._lines(created_from=datetime(2021, 1, 1),
                           created_to=datetime(2023, 1, 1))
        self.assertEqual({r['move'] for r in rows}, {mid.name})

    def test_the_bound_is_the_entrys_stamp_not_the_document_date(self):
        # Both invoices carry the SAME invoice_date; only the typing moment
        # differs. If the filter were reading the document date, neither the
        # include nor the exclude below could hold.
        old = self._invoice(100.0, created_at='2020-01-01 08:00:00')
        fresh = self._invoice(200.0)
        self.assertEqual(old.invoice_date, fresh.invoice_date)
        rows = self._lines(created_from=datetime(2024, 1, 1))
        self.assertEqual({r['move'] for r in rows}, {fresh.name})

    def test_an_empty_bound_means_no_bound(self):
        self._invoice(100.0, created_at='2020-01-01 08:00:00')
        self._invoice(200.0)
        self.assertEqual(len(self._lines(created_from=None, created_to=None)), 2,
                         "a falsy bound must not read as the epoch")

    # ------------------------------------------------------- through the UI
    def _wizard(self, **kw):
        return self.env['epg.partner.statement.wizard'].create(dict({
            'partner_ids': [(6, 0, self.partner.ids)],
            'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31),
            'min_outstanding': 0.0}, **kw))

    def test_the_wizard_hands_the_bounds_to_the_engine(self):
        wiz = self._wizard(created_from=datetime(2024, 1, 1),
                           created_to=datetime(2027, 1, 1))
        options = wiz._options()
        self.assertEqual(options['created_from'], datetime(2024, 1, 1))
        self.assertEqual(options['created_to'], datetime(2027, 1, 1))

    def test_a_backwards_window_is_refused_not_silently_empty(self):
        wiz = self._wizard(created_from=datetime(2027, 1, 1),
                           created_to=datetime(2024, 1, 1))
        with self.assertRaises(UserError):
            wiz._options()

    def test_the_printed_report_carries_the_filter(self):
        self._invoice(100.0, created_at='2020-01-01 08:00:00')
        fresh = self._invoice(200.0)
        wiz = self._wizard(created_from=datetime(2024, 1, 1))
        data = wiz.action_print()['data']
        self.assertIn('created_from', data,
                      "a PDF that ignores the filter disagrees with its own screen")
        options = self.Engine.normalize_options(data)
        self.assertEqual(options['created_from'], datetime(2024, 1, 1))
        rebuilt = self.Engine.compute(self.partner, options)
        rows = [r for b in rebuilt[self.partner.id]['blocks'] for r in b['lines']]
        self.assertEqual({r['move'] for r in rows}, {fresh.name})

    def test_the_selection_skips_a_clinic_with_nothing_in_the_window(self):
        # Every entry this clinic has was typed during the import, so a run
        # bounded to "entered since 2024" must not pick it and print a page
        # with nothing on it.
        self._invoice(100.0, created_at='2020-01-01 08:00:00')
        wiz = self._wizard(created_from=datetime(2024, 1, 1),
                           min_outstanding=1.0)
        self.assertNotIn(self.partner, wiz._partners())
        wide = self._wizard(min_outstanding=1.0)
        self.assertIn(self.partner, wide._partners())


@tagged('post_install', '-at_install')
class TestMigratedDocumentNumber(TransactionCase):
    """The Document column carries the number the clinic knows.

    Everything brought over from Odoo 10 sits on one journal entry per clinic, so
    forty rows of a statement all read "MISC/26-27/04/4601" while the doctor's own
    invoice number - the one they ask about on the phone - was pushed into
    Description. (client, 2026-09-18)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Engine = cls.env['epg.partner.statement']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Migrated Clinic', 'is_company': True, 'property_payment_term_id': False})
        cls.product = cls.env['product.product'].create(
            {'name': 'Hawleys Appliance', 'type': 'service', 'list_price': 100})
        cls.day = date(2026, 6, 15)

    def _opening_entry(self, numbers):
        """One journal entry carrying several old documents, as the migration made it."""
        receivable = self.env['account.account'].search(
            [('account_type', '=', 'asset_receivable'), ('company_ids', 'in', self.company.id)], limit=1)
        income = self.env['account.account'].search(
            [('account_type', '=', 'income'), ('company_ids', 'in', self.company.id)], limit=1)
        lines = []
        for number, amount in numbers:
            lines.append((0, 0, {'account_id': receivable.id, 'partner_id': self.partner.id,
                                 'name': number, 'debit': amount, 'credit': 0.0,
                                 'date_maturity': self.day}))
        lines.append((0, 0, {'account_id': income.id, 'name': 'Opening balance',
                             'debit': 0.0, 'credit': sum(a for _n, a in numbers)}))
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'date': self.day, 'ref': 'Opening Balance (Odoo 10 migration)',
            'line_ids': lines})
        entry.action_post()
        entry.line_ids.filtered(lambda l: l.account_id == receivable).write(
            {'original_date': self.day})
        return entry

    def _rows(self):
        options = {'statement_type': 'receivable', 'date_from': date(2026, 1, 1),
                   'date_to': date(2026, 12, 31), 'company': self.company,
                   'open_items_only': False, 'show_ageing': False}
        statement = self.Engine.compute(self.partner, options)[self.partner.id]
        return statement['blocks'][0]['lines']

    def test_the_old_invoice_number_is_the_document(self):
        entry = self._opening_entry([('OC132437', 330.0), ('CNRB/2024/3960', 660.0)])
        rows = {row['move']: row for row in self._rows()}
        self.assertEqual(set(rows), {'OC132437', 'CNRB/2024/3960'},
                         "the clinic's own numbers, not one journal entry twice")
        for row in rows.values():
            self.assertEqual(row['entry'], entry.name, "the entry is still there to be shown")
            self.assertEqual(row['name'], '', "and the number is not repeated in Description")

    def test_a_real_invoice_keeps_its_own_number_and_says_what_it_was_for(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': self.day, 'invoice_date_due': self.day, 'date': self.day,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1,
                                         'price_unit': 500.0, 'tax_ids': [(6, 0, [])]})]})
        invoice.action_post()
        [row] = [r for r in self._rows() if r['move'] == invoice.name]
        self.assertFalse(row['old_number'], "nothing migrated about it")
        self.assertIn('Hawleys Appliance', row['name'], "what the invoice was for")
        self.assertEqual(row['entry'], invoice.name)

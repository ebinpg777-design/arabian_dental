# -*- coding: utf-8 -*-
"""Regressions found in the review of 2026-10-13: each test failed before its fix.

The collections tests run AS the people who use the desk - an invoicing clerk with no
administrator rights and no right to edit contacts - because every other test here runs
as the superuser, which is how reminders came to fail for everyone but an administrator
without a test noticing."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestReviewFixes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'tax_lock_date': False, 'sale_lock_date': False,
                           'purchase_lock_date': False, 'ebshel_followup_min_amount': 0.0,
                           'email': 'collections@example.com'})
        cls.today = fields.Date.context_today(cls.env.user)
        Account = cls.env['account.account']

        def account(code, name, atype):
            return Account.search([('code', '=', code)], limit=1) or Account.create(
                {'code': code, 'name': name, 'account_type': atype})
        cls.income = account('ZZ7010', 'Test income', 'income')
        cls.charges = account('ZZ6020', 'Test bank charges', 'expense')
        cls.general = cls.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.bank = cls.env['account.journal'].create({'name': 'ERF Test Bank', 'type': 'bank', 'code': 'ERFB'})
        Level = cls.env['ebshel.followup.level']
        Level.search([('company_id', '=', cls.company.id)]).write({'active': False})
        cls.l1 = Level.create({'name': 'F1 friendly', 'days': 7, 'action': 'email', 'auto': True,
                               'attach_statement': False, 'attach_invoices': False})
        cls.l2 = Level.create({'name': 'F2 final', 'days': 45, 'action': 'letter', 'repeat_days': 15})
        cls.customer = cls.env['res.partner'].create({'name': 'Review Clinic', 'email': 'review@example.com',
                                                      'property_payment_term_id': False})
        # the people: the desk's clerk, a reader, and someone outside accounting
        cls.clerk = new_test_user(cls.env, login='erf_clerk', groups='base.group_user,account.group_account_invoice',
                                  company_id=cls.company.id)
        cls.reader = new_test_user(cls.env, login='erf_reader', groups='base.group_user,account.group_account_readonly',
                                   company_id=cls.company.id)
        cls.plain = new_test_user(cls.env, login='erf_plain', groups='base.group_user', company_id=cls.company.id)

    def _invoice(self, partner, amount, date, due=None):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': date,
            'invoice_payment_term_id': False, 'invoice_date_due': due or date,
            'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': amount,
                                         'account_id': self.income.id, 'tax_ids': [(6, 0, [])]})]})
        move.action_post()
        return move

    def _overdue(self, partner=None, amount=500.0):
        return self._invoice(partner or self.customer, amount, self.today - timedelta(days=20), due=self.today - timedelta(days=10))

    def _st(self, amount, label, **extra):
        return self.env['account.bank.statement.line'].create(dict(
            {'journal_id': self.bank.id, 'date': self.today, 'payment_ref': label, 'amount': amount}, **extra))

    # ------------------------------------------------------------------ collections, as the clerk
    def test_the_clerk_sends_a_reminder_without_admin_rights(self):
        """mail.mail is base.group_system's alone in Odoo 20, and the follow-up fields live
        on contacts the accounting team may not edit: the clerk's reminder died on both."""
        self.assertFalse(self.clerk.has_group('base.group_system'))
        self.assertFalse(self.clerk.has_group('base.group_partner_manager'))
        self._overdue()
        self.env.invalidate_all()
        result = self.customer.with_user(self.clerk).action_ebshel_send_reminder(level_id=self.l1.id)
        self.assertEqual(result['sent'], [self.customer.id], result)
        self.assertEqual(self.customer.ebshel_followup_level_id, self.l1)
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=38))
        log = self.customer.ebshel_followup_action_ids
        self.assertEqual(log.user_id, self.clerk, "logged as the clerk, not as the superuser")
        self.assertEqual(log.mail_id.email_to, 'review@example.com')
        self.assertIn('collections@example.com', log.mail_id.email_from, "sent from the company")

    def test_the_clerk_sends_a_template_reminder_with_its_attachments(self):
        template = self.env.ref('ebshel_account_advanced.mail_template_reminder')
        self.l1.write({'template_id': template.id, 'attach_statement': True, 'attach_invoices': True})
        self._overdue()
        self.env.invalidate_all()
        self.customer.with_user(self.clerk).action_ebshel_send_reminder(level_id=self.l1.id)
        mail = self.customer.ebshel_followup_action_ids.mail_id
        self.assertTrue(mail)
        self.assertTrue(mail.attachment_ids, "the statement and the invoices, attached to a contact the clerk cannot edit")
        self.assertIn('collections@example.com', mail.email_from)

    def test_the_clerk_works_the_desk(self):
        self._overdue()
        self.env.invalidate_all()
        Desk = self.env['ebshel.followup.desk'].with_user(self.clerk)
        Desk.log_call(self.customer.id, 'called', next_date=fields.Date.to_string(self.today + timedelta(days=3)))
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=3))
        Desk.promise(self.customer.id, fields.Date.to_string(self.today + timedelta(days=5)), 500.0, 'cheque')
        self.assertEqual(self.customer.ebshel_promise_date, self.today + timedelta(days=5))
        Desk.snooze(self.customer.id, 2)
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=2))
        Desk.set_flag(self.customer.id, 'on_hold', True)
        Desk.set_flag(self.customer.id, 'excluded', True)
        self.assertTrue(self.customer.ebshel_on_hold and self.customer.ebshel_followup_excluded)
        Desk.set_responsible(self.customer.id, self.clerk.id)
        Desk.save_note(self.customer.id, 'calls after three')
        self.assertEqual(self.customer.ebshel_followup_responsible_id, self.clerk)
        self.assertEqual(self.customer.ebshel_followup_note, 'calls after three')
        action = Desk.with_context(discard_logo_check=True).letter(self.customer.ids, self.l2.id)
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(self.customer.ebshel_followup_level_id, self.l2)

    def test_the_desk_still_turns_away_who_may_not_act(self):
        """The writes run as the desk now - so its own check is all that stands in the way."""
        self._overdue()
        self.env.invalidate_all()
        for user in (self.reader, self.plain):
            with self.assertRaises(AccessError):
                self.env['ebshel.followup.desk'].with_user(user).save_note(self.customer.id, 'not mine to write')
            for call in (lambda p: p.action_ebshel_set_hold(True), lambda p: p.action_ebshel_snooze(5),
                         lambda p: p.action_ebshel_send_reminder(level_id=self.l1.id),
                         lambda p: p.action_ebshel_promise(fields.Date.to_string(self.today), 1.0)):
                with self.assertRaises(AccessError):
                    call(self.customer.with_user(user))
        self.assertFalse(self.customer.ebshel_on_hold)
        self.assertFalse(self.customer.ebshel_followup_note)
        self.assertFalse(self.customer.ebshel_followup_action_ids)

    def test_automatic_reminders_go_out_from_the_company_not_odoobot(self):
        """The nightly job runs as OdooBot, and its reminders went out under OdooBot's name."""
        self.company.ebshel_followup_auto = True
        self._overdue()
        self.env['ebshel.followup.desk']._cron_followups()
        mail = self.customer.ebshel_followup_action_ids.mail_id
        self.assertTrue(mail)
        self.assertIn('collections@example.com', mail.email_from)
        self.assertNotIn(self.env.ref('base.partner_root').email or 'odoobot', mail.email_from)

    def test_collection_fields_are_out_of_reach_outside_accounting(self):
        """The fields and filters read the follow-up levels, which only the accounting team
        may read: a salesperson's contact list or search died with an access error."""
        Partner = self.env['res.partner']
        self.assertFalse(Partner.with_user(self.plain).fields_get(['ebshel_followup_state', 'ebshel_followup_level_id']))
        self.assertTrue(Partner.with_user(self.reader).fields_get(['ebshel_followup_state']))
        plain_views = Partner.with_user(self.plain).get_views([(False, 'search'), (False, 'form')])['views']
        self.assertNotIn('ebshel_action', plain_views['search']['arch'])
        self.assertNotIn('ebshel_followup_state', plain_views['form']['arch'])
        reader_views = Partner.with_user(self.reader).get_views([(False, 'search')])['views']
        self.assertIn('ebshel_action', reader_views['search']['arch'])

    def test_a_contact_is_found_by_its_companys_collection_status(self):
        """The compute judges a contact on its company's ledger; the search matched only the
        company itself - the list showed 'Reminder due' on a contact the filter left out."""
        contact = self.env['res.partner'].create({'name': 'Review Accounts', 'parent_id': self.customer.id})
        self._overdue()
        self.env.invalidate_all()
        self.assertEqual(contact.ebshel_followup_state, 'action')
        Partner = self.env['res.partner']
        self.assertIn(contact, Partner.search([('ebshel_followup_state', '=', 'action'), ('id', '=', contact.id)]))
        self.assertFalse(Partner.search([('ebshel_followup_state', '=', 'none'), ('id', '=', contact.id)]))
        self.assertIn(contact, Partner.search([('ebshel_followup_state', '!=', 'none'), ('id', '=', contact.id)]))

    # ------------------------------------------------------------------ printing
    def test_the_print_data_survives_the_layout_wizard(self):
        """report_action() hands an administrator the document layout wizard when the
        company has none: data and context patched onto the returned action were lost."""
        self.company.external_report_layout_id = False
        day = fields.Date.to_string(self.today - timedelta(days=1))
        action = self.env['ebshel.bank.desk'].brs_pdf(self.bank.id, day)
        report_action = action['context']['report_action'] if action['type'] != 'ir.actions.report' else action
        self.assertEqual(report_action['data']['date'], day)
        action = self.customer.action_ebshel_print_letter(level_id=self.l2.id, log=False)
        report_action = action['context']['report_action'] if action['type'] != 'ir.actions.report' else action
        self.assertEqual(report_action['context']['ebshel_level_id'], self.l2.id)
        # and without the wizard
        action = self.env['ebshel.bank.desk'].with_context(discard_logo_check=True).brs_pdf(self.bank.id, day)
        self.assertEqual((action['type'], action['data']['date']), ('ir.actions.report', day))

    # ------------------------------------------------------------------ bank
    def test_matching_the_rest_of_a_bank_line_keeps_the_first_match(self):
        """match_bank deleted the lines an earlier match had put on the bank entry, while
        counting only the suspense amount: the first invoice came unpaid and the entry
        went out of balance."""
        first = self._overdue(amount=600.0)
        second = self._overdue(amount=400.0)
        ar = lambda m: m.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')   # noqa: E731
        st = self._st(1000.0, 'two invoices', partner_id=self.customer.id)
        self.env['ebshel.bank.desk'].reconcile(st.id, [{'kind': 'open', 'aml_id': ar(first).id}])
        self.assertTrue(ar(first).reconciled)
        res = self.env['ebshel.match.desk'].match_bank(st.id, ar(second).ids)
        self.assertTrue(res['reconciled'])
        self.assertTrue(ar(first).reconciled, "the first match stands")
        self.assertTrue(ar(second).reconciled)
        lines = st.move_id.line_ids
        self.assertAlmostEqual(sum(lines.mapped('debit')), sum(lines.mapped('credit')), 2)
        self.assertAlmostEqual(sum(lines.mapped('debit')), 1000.0, 2)

    def test_one_line_that_cannot_be_reconciled_does_not_stop_the_rest(self):
        """A failing line left its savepoint with the error and aborted the whole run: a
        foreign-currency line, or a rule pointing at the bank account itself."""
        Rule = self.env['ebshel.bank.rule']
        Rule.create({'name': 'charges', 'keywords': 'charges', 'account_id': self.charges.id, 'sequence': 20,
                     'journal_ids': [(6, 0, self.bank.ids)]})
        Rule.create({'name': 'loop', 'keywords': 'loopback', 'account_id': self.bank.default_account_id.id, 'sequence': 10,
                     'journal_ids': [(6, 0, self.bank.ids)]})
        other = self.env['res.currency'].with_context(active_test=False).search(
            [('id', '!=', self.company.currency_id.id)], limit=1)
        other.active = True
        foreign = self._st(-50.0, 'FX CHARGES', foreign_currency_id=other.id, amount_currency=-45.0)
        looped = self._st(-30.0, 'LOOPBACK FEE')
        charge = self._st(-20.0, 'SMS CHARGES')
        done = self.env['ebshel.bank.desk'].auto_reconcile(self.bank.id)
        self.assertEqual(done['rules'], 1)
        self.assertTrue(charge.is_reconciled)
        self.assertFalse(foreign.is_reconciled)
        self.assertFalse(looped.is_reconciled)
        # pasted rows: the import stands even when a rule fails on one of them
        res = self.env['ebshel.bank.desk'].import_rows(self.bank.id, [
            {'date': fields.Date.to_string(self.today), 'label': 'LOOPBACK AGAIN', 'amount': -31.0},
            {'date': fields.Date.to_string(self.today), 'label': 'MORE CHARGES', 'amount': -21.0}], tick=False)
        self.assertEqual((res['created'], res['ruled']), (2, 1))

    # ------------------------------------------------------------------ deferrals and entries
    def test_a_cancelled_deferral_does_not_block_a_new_one(self):
        self.company.write({'ebshel_deferral_auto': False})
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': self.today,
            'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'name': 'Support contract', 'quantity': 1, 'price_unit': 1200.0,
                                         'account_id': self.income.id, 'tax_ids': [(6, 0, [])],
                                         'ebshel_deferral_start': self.today,
                                         'ebshel_deferral_end': self.today + timedelta(days=364)})]})
        move.action_post()
        first = move.ebshel_deferral_ids
        self.assertEqual(len(first), 1)
        first.state = 'cancelled'
        move._ebshel_create_deferrals()
        fresh = move.ebshel_deferral_ids - first
        self.assertEqual(len(fresh), 1, "the line is deferred again")
        self.assertEqual(fresh.state, 'draft')
        move._ebshel_create_deferrals()
        self.assertEqual(len(move.ebshel_deferral_ids), 2, "but only once")

    def test_the_studio_warns_of_every_lock_posting_would_hit(self):
        """The pre-check knew only the fiscal year lock: a sales journal past the sales
        lock passed the check and failed at posting."""
        sale = self.env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', self.company.id)], limit=1)
        self.company.sale_lock_date = '2000-01-31'
        Studio = self.env['ebshel.entry.studio']
        vals = {'journal_id': sale.id, 'date': '2000-01-15',
                'lines': [{'account_id': self.charges.id, 'debit': 10.0}, {'account_id': self.income.id, 'credit': 10.0}]}
        self.assertTrue(any('lock date' in p for p in Studio.check(vals)['problems']))
        self.assertFalse(any('lock date' in p for p in Studio.check(dict(vals, journal_id=self.general.id))['problems']),
                         "the sales lock leaves a general journal alone")

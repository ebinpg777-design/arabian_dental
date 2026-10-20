# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReceiptNumbers(TransactionCase):
    """A journal's first Odoo 20 receipt continues the receipts migrated from v17
    (client, 2026-10-20: receipts started PBNK1/26-27/0001 - "continue the old series")."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.fiscalyear_last_month = '3'      # the lab's April-March year
        cls.env.company.fiscalyear_last_day = 31
        Journal = cls.env['account.journal']
        cls.migrated = Journal.create({'name': 'RN Migrated Bank', 'code': 'RNMB', 'type': 'bank'})
        cls.fresh = Journal.create({'name': 'RN Fresh Bank', 'code': 'RNFB', 'type': 'bank'})
        cls.partner = cls.env['res.partner'].create({'name': 'RN Clinic'})
        cls.income = cls.env['account.account'].search(
            [('account_type', '=', 'income'), ('company_ids', 'in', cls.env.company.id)], limit=1)
        # a receipt as the migration brings it: a plain entry carrying v17's number
        replay = cls.env(context=dict(cls.env.context, migration_replay=True))
        entry = replay['account.move'].create({
            'move_type': 'entry', 'journal_id': cls.migrated.id, 'date': '2026-10-10',
            'line_ids': [
                (0, 0, {'account_id': cls.migrated.default_account_id.id, 'debit': 50.0, 'name': 'receipt'}),
                (0, 0, {'account_id': cls.income.id, 'credit': 50.0, 'name': 'receipt'}),
            ]})
        entry.name = 'PRNMB/2026-27/0040'
        entry.action_post()
        cls.entry = entry

    def _receipt(self, journal, date='2026-10-20'):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner.id,
            'amount': 10.0, 'journal_id': journal.id, 'date': date})
        payment.action_post()
        return payment

    def test_the_migrated_entry_is_what_it_should_be(self):
        self.assertEqual(self.entry.name, 'PRNMB/2026-27/0040')
        self.assertFalse(self.entry.origin_payment_id)
        self.assertTrue(self.migrated.payment_sequence)

    def test_the_first_receipt_continues_the_migrated_series(self):
        self.assertEqual(self._receipt(self.migrated).name, 'PRNMB/2026-27/0041')

    def test_the_next_receipts_follow_it(self):
        self._receipt(self.migrated)
        self.assertEqual(self._receipt(self.migrated).name, 'PRNMB/2026-27/0042')

    def test_a_journal_without_migrated_receipts_numbers_as_odoo_does(self):
        self.assertEqual(self._receipt(self.fresh).name, 'PRNFB/26-27/0001')

    def test_another_fiscal_year_is_not_continued(self):
        # the migrated receipt is 2026-27; a receipt dated in 2027-28 starts that year's series
        self.assertNotIn('2026-27', self._receipt(self.migrated, date='2027-05-02').name)

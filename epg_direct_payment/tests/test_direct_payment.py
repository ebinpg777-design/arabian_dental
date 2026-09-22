# -*- coding: utf-8 -*-
"""A direct payment posts against an Expense/Income account, not receivable/payable.

The base account.payment already has a destination_account_id and a
_prepare_move_counterpart_lines that just uses it - the only things standing
between that and an expense/income posting are the field's domain (a form-view
restriction, not enforced by the ORM), and _get_valid_payment_account_types(),
which is what decides whether the posted line reads back as the counterpart or
as an unexplained write-off. Both are what this module changes, and both are
what these tests are really pinned on.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDirectPayment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Payment = cls.env['account.payment']

        cls.expense_account = cls.env['account.account'].create({
            'name': 'Test Direct Expense', 'code': 'TDEXP',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.income_account = cls.env['account.account'].create({
            'name': 'Test Direct Income', 'code': 'TDINC',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.asset_account = cls.env['account.account'].create({
            'name': 'Test Direct Not Allowed', 'code': 'TDNOK',
            'account_type': 'asset_current',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Direct Bank', 'type': 'bank', 'code': 'TDBK',
                'company_id': cls.company.id,
            })

    def _payment(self, **vals):
        return self.Payment.create({
            'payment_kind': 'direct',
            'payment_type': 'outbound',
            'journal_id': self.journal.id,
            'destination_account_id': self.expense_account.id,
            'amount': 250.0,
            **vals,
        })

    # ---------------------------------------------------------------- posting
    def test_a_direct_payment_posts_against_the_expense_account_with_no_partner(self):
        payment = self._payment()
        payment.action_post()
        self.assertIn(payment.state, ('in_process', 'paid'))
        self.assertTrue(payment.move_id)
        self.assertEqual(payment.move_id.state, 'posted')

        counterpart = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == self.expense_account)
        self.assertEqual(len(counterpart), 1,
                         "the expense account must carry exactly one line - the "
                         "counterpart to the bank line, not a write-off")
        self.assertAlmostEqual(counterpart.balance, 250.0)
        self.assertFalse(counterpart.partner_id,
                         "a direct payment names no partner on its own line")

    def test_an_inbound_direct_payment_posts_against_income(self):
        payment = self._payment(
            payment_type='inbound', destination_account_id=self.income_account.id)
        payment.action_post()
        counterpart = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == self.income_account)
        self.assertEqual(len(counterpart), 1)
        self.assertAlmostEqual(counterpart.balance, -250.0,
                               "money received is a credit to income")

    def test_the_expense_line_is_read_back_as_the_counterpart_not_a_writeoff(self):
        """The defect this module has to avoid: without extending
        _get_valid_payment_account_types, _seek_for_lines drops the expense line
        into writeoff_lines, and any later edit to the payment (amount, date...)
        would silently lose it."""
        payment = self._payment()
        payment.action_post()
        liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        self.assertEqual(counterpart_lines.account_id, self.expense_account)
        self.assertFalse(writeoff_lines)

    # -------------------------------------------------------------- validation
    def test_a_non_expense_income_account_is_refused(self):
        # The constraint fires on write, same as create - so the record never
        # gets to exist with this combination, not just refused at posting.
        with self.assertRaises(ValidationError):
            self._payment(destination_account_id=self.asset_account.id)

    # ------------------------------------------------------- existing flow intact
    def test_a_plain_customer_payment_is_unaffected(self):
        """payment_kind defaults to 'partner' - the ordinary flow must still
        resolve destination_account_id from the partner, exactly as core does."""
        clinic = self.env['res.partner'].create({'name': 'Test Direct Clinic'})
        payment = self.Payment.create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': clinic.id,
            'journal_id': self.journal.id,
            'amount': 100.0,
        })
        self.assertEqual(payment.payment_kind, 'partner')
        self.assertEqual(
            payment.destination_account_id,
            clinic.with_company(self.company).property_account_receivable_id)

    def test_partner_type_is_steered_off_customer_for_a_direct_payment(self):
        """Other installed modules can key a requirement off partner_type ==
        'customer' (e.g. lab_collections' Sales Route, required on an inbound
        customer payment) - a direct payment has no partner at all, so it must
        not carry a partner_type value that trips those by coincidence."""
        payment = self._payment(payment_type='inbound')
        self.assertEqual(payment.partner_type, 'supplier')

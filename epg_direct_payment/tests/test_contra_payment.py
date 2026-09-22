# -*- coding: utf-8 -*-
"""A contra payment moves money between two of the company's own journals.

Posting one leg has to raise its mirror on the other journal, both against the
company's Internal Transfer account, and leave that account reconciled back to
zero - otherwise the transfer just sits there as two unrelated open lines and
the accountant has to go find and match them by hand, which is exactly the
manual step this feature exists to remove.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestContraPayment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Payment = cls.env['account.payment']
        cls.bank_a = cls.env['account.journal'].create({
            'name': 'Test Contra Bank A', 'type': 'bank', 'code': 'TCBA',
            'company_id': cls.company.id,
        })
        cls.bank_b = cls.env['account.journal'].create({
            'name': 'Test Contra Bank B', 'type': 'bank', 'code': 'TCBB',
            'company_id': cls.company.id,
        })

    def _payment(self, **vals):
        return self.Payment.create({
            'payment_kind': 'contra',
            'payment_type': 'outbound',
            'journal_id': self.bank_a.id,
            'destination_journal_id': self.bank_b.id,
            'amount': 500.0,
            **vals,
        })

    # ---------------------------------------------------------------- posting
    def test_posting_one_leg_raises_and_posts_its_mirror(self):
        payment = self._payment()
        payment.action_post()

        mirror = payment.paired_internal_transfer_payment_id
        self.assertTrue(mirror, "the other leg must be raised automatically")
        self.assertEqual(mirror.paired_internal_transfer_payment_id, payment,
                         "the pairing must read both ways")
        self.assertEqual(mirror.journal_id, self.bank_b)
        self.assertEqual(mirror.destination_journal_id, self.bank_a)
        self.assertEqual(mirror.payment_type, 'inbound',
                         "money leaving A arrives as money received on B")
        self.assertEqual(mirror.amount, 500.0)
        self.assertFalse(mirror.partner_id)
        self.assertEqual(mirror.state, payment.state)
        self.assertTrue(mirror.move_id)
        self.assertEqual(mirror.move_id.state, 'posted')

    def test_both_legs_post_against_the_transfer_account_and_net_to_zero(self):
        payment = self._payment()
        payment.action_post()
        mirror = payment.paired_internal_transfer_payment_id
        transfer_account = self.company.transfer_account_id

        own_line = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == transfer_account)
        mirror_line = mirror.move_id.line_ids.filtered(
            lambda l: l.account_id == transfer_account)
        self.assertEqual(len(own_line), 1)
        self.assertEqual(len(mirror_line), 1)
        self.assertAlmostEqual(own_line.balance + mirror_line.balance, 0.0,
                               "the two legs must cancel out on the transfer account")
        self.assertTrue(own_line.reconciled, "left open, this is two unrelated "
                        "entries, not a transfer")
        self.assertTrue(mirror_line.reconciled)
        self.assertEqual(own_line.full_reconcile_id, mirror_line.full_reconcile_id)

    def test_partner_type_is_steered_off_customer_on_both_legs(self):
        """The mirror is raised by _create_contra_mirror, not the form's onchange
        - this is what proves the create() override covers that path too."""
        payment = self._payment(payment_type='inbound')
        self.assertEqual(payment.partner_type, 'supplier')
        payment.action_post()
        self.assertEqual(payment.paired_internal_transfer_payment_id.partner_type,
                         'supplier')

    def test_the_bank_lines_move_the_full_amount_each_side(self):
        payment = self._payment()
        payment.action_post()
        mirror = payment.paired_internal_transfer_payment_id

        out_line = payment._seek_for_lines()[0]
        in_line = mirror._seek_for_lines()[0]
        self.assertAlmostEqual(out_line.balance, -500.0,
                               "money leaving the source journal is a credit")
        self.assertAlmostEqual(in_line.balance, 500.0,
                               "money arriving on the destination journal is a debit")

    # -------------------------------------------------------------- validation
    def test_refused_with_no_destination_journal(self):
        payment = self._payment(destination_journal_id=False)
        with self.assertRaises(UserError):
            payment.action_post()

    def test_refused_when_destination_is_the_same_journal(self):
        payment = self._payment(destination_journal_id=self.bank_a.id)
        with self.assertRaises(UserError):
            payment.action_post()

    def test_refused_with_no_transfer_account_configured(self):
        self.company.transfer_account_id = False
        payment = self._payment()
        with self.assertRaises(UserError):
            payment.action_post()

    # ----------------------------------------------------------------- cancel
    def test_cancelling_one_leg_cancels_its_pair(self):
        payment = self._payment()
        payment.action_post()
        mirror = payment.paired_internal_transfer_payment_id
        payment.action_cancel()
        self.assertEqual(payment.state, 'canceled')
        self.assertEqual(mirror.state, 'canceled',
                         "a lone cancelled leg would leave the other posted "
                         "with nothing on the far end")

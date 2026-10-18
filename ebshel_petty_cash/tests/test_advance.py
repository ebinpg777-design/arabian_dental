# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import PettyCashCommon


@tagged("post_install", "-at_install")
class TestPettyCashAdvance(PettyCashCommon):

    def _paid_advance(self, amount, **vals):
        advance = (
            self.env["ebshel.petty.cash.advance"]
            .with_user(self.member)
            .create(
                {
                    "fund_id": self.fund.id,
                    "holder_id": self.member.partner_id.id,
                    "purpose": "Site visit",
                    "amount": amount,
                    **vals,
                }
            )
        )
        advance.action_submit()
        advance.with_user(self.approver).action_approve()
        advance.with_user(self.custodian).action_pay()
        return advance

    def _settle(self, advance):
        wizard = (
            self.env["ebshel.petty.cash.advance.settle.wizard"]
            .with_user(self.custodian)
            .create({"advance_id": advance.id})
        )
        wizard.action_settle()
        return wizard

    def test_pay_out_moves_cash_to_the_advance_account(self):
        advance = self._paid_advance(300.0)
        self.assertEqual(advance.state, "paid")
        self.assertEqual(self._balance(), 700.0)
        account = self.company.petty_cash_advance_account_id
        self.assertTrue(account.reconcile)
        line = advance.move_id.sudo().line_ids.filtered(lambda aml: aml.account_id == account)
        self.assertEqual(line.balance, 300.0)
        self.assertEqual(line.partner_id, self.member.partner_id)
        self.assertEqual(advance.amount_due, 300.0)
        self.fund.invalidate_recordset()
        self.assertEqual(self.fund.advance_outstanding, 300.0)

    def test_vouchers_against_the_advance_then_return_the_rest(self):
        advance = self._paid_advance(300.0)
        voucher_1 = self._voucher([(self.cat_office, 115.0)], advance_id=advance.id)
        voucher_2 = self._voucher([(self.cat_travel, 55.0)], advance_id=advance.id)
        self._approve_and_pay(voucher_1)
        self._approve_and_pay(voucher_2)
        # vouchers against an advance do not touch the cash box
        self.assertEqual(self._balance(), 700.0)
        self.assertEqual(advance.amount_spent, 170.0)
        self.assertEqual(advance.amount_due, 130.0)

        wizard = self._settle(advance)
        self.assertEqual(wizard.direction, "return")
        self.assertEqual(advance.state, "settled")
        self.assertEqual(advance.settlement_amount, 130.0)
        self.assertEqual(self._balance(), 830.0)
        account = self.company.petty_cash_advance_account_id
        lines = (advance.move_id | advance.settlement_move_id | voucher_1.move_id | voucher_2.move_id).line_ids.filtered(
            lambda aml: aml.account_id == account
        )
        self.assertTrue(all(lines.mapped("reconciled")))
        self.assertAlmostEqual(sum(lines.mapped("balance")), 0.0)

    def test_overspending_is_reimbursed(self):
        advance = self._paid_advance(100.0)
        self._approve_and_pay(self._voucher([(self.cat_travel, 130.0)], advance_id=advance.id))
        wizard = self._settle(advance)
        self.assertEqual(wizard.direction, "reimburse")
        self.assertEqual(advance.settlement_amount, -30.0)
        self.assertEqual(self._balance(), 870.0)

    def test_exact_spending_settles_without_entry(self):
        advance = self._paid_advance(50.0)
        self._approve_and_pay(self._voucher([(self.cat_travel, 50.0)], advance_id=advance.id))
        self._settle(advance)
        self.assertEqual(advance.state, "settled")
        self.assertFalse(advance.settlement_move_id)

    def test_pending_vouchers_block_the_settlement(self):
        advance = self._paid_advance(100.0)
        self._voucher([(self.cat_travel, 30.0)], advance_id=advance.id).action_submit()
        with self.assertRaises(UserError):
            self._settle(advance)

    def test_advance_cannot_exceed_the_cash(self):
        with self.assertRaises(UserError):
            self._paid_advance(1200.0)

    def test_overdue_holder_cannot_get_another_advance(self):
        advance = self._paid_advance(100.0)
        advance.write({"date": self.today - timedelta(days=10), "due_date": self.today - timedelta(days=1)})
        self.assertTrue(advance.is_overdue)
        self.assertIn(
            advance,
            self.env["ebshel.petty.cash.advance"].search([("is_overdue", "=", True)]),
        )
        new = self.env["ebshel.petty.cash.advance"].create(
            {
                "fund_id": self.fund.id,
                "holder_id": self.member.partner_id.id,
                "purpose": "Again",
                "amount": 10.0,
            }
        )
        with self.assertRaises(UserError):
            new.action_submit()
        self.env["ebshel.petty.cash.fund"]._cron_petty_cash_controls()
        settle = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_settle")
        self.assertIn(settle, advance.activity_ids.activity_type_id)

    def test_cancel_paid_advance(self):
        advance = self._paid_advance(100.0)
        voucher = self._voucher([(self.cat_travel, 10.0)], advance_id=advance.id)
        self._approve_and_pay(voucher)
        with self.assertRaises(UserError):
            advance.with_user(self.custodian).action_cancel()
        voucher.with_user(self.custodian).action_cancel()
        advance.with_user(self.custodian).action_cancel()
        self.assertEqual(advance.state, "cancelled")
        self.assertEqual(self._balance(), 1000.0)

    def test_default_due_date(self):
        self.company.petty_cash_advance_due_days = 10
        advance = self.env["ebshel.petty.cash.advance"].create(
            {
                "fund_id": self.fund.id,
                "holder_id": self.member.partner_id.id,
                "purpose": "Trip",
                "amount": 10.0,
                "date": self.today,
            }
        )
        self.assertEqual(advance.due_date, self.today + timedelta(days=10))

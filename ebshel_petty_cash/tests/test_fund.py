# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import PettyCashCommon


@tagged("post_install", "-at_install")
class TestPettyCashFund(PettyCashCommon):

    def test_fund_gets_its_own_cash_journal(self):
        journal = self.fund.journal_id
        self.assertEqual(journal.type, "cash")
        self.assertEqual(self.fund.account_id, journal.default_account_id)
        self.assertEqual(self.fund.account_id.account_type, "asset_cash")
        other = self._create_fund("Warehouse", "PCWH")
        self.assertNotEqual(other.journal_id, journal)
        self.assertNotEqual(other.account_id, self.fund.account_id)

    def test_top_up_moves_bank_money_into_the_fund(self):
        self.assertEqual(self.fund.balance, 1000.0)
        self.assertEqual(self.fund.replenish_amount, 0.0)
        self.assertEqual(self.fund.balance_ratio, 100.0)
        replenishment = self.env["ebshel.petty.cash.replenishment"].search(
            [("fund_id", "=", self.fund.id)]
        )
        move = replenishment.move_id
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.journal_id, self.fund.journal_id)
        self.assertEqual(move.petty_cash_fund_id, self.fund)
        fund_line = move.line_ids.filtered(lambda line: line.account_id == self.fund.account_id)
        self.assertEqual(fund_line.balance, 1000.0)
        # the counterpart is the bank's outstanding payments account, not the bank account itself
        other_line = move.line_ids - fund_line
        self.assertEqual(other_line.account_id, replenishment.counterpart_account_id)
        self.assertNotEqual(other_line.account_id, self.bank_journal.default_account_id)

    def test_balance_is_read_from_the_ledger(self):
        """Any posted entry on the fund account counts, drafts do not."""
        move = self.env["account.move"].create(
            {
                "journal_id": self.fund.journal_id.id,
                "line_ids": [
                    Command.create({"account_id": self.fund.account_id.id, "balance": 50.0}),
                    Command.create({"account_id": self.expense_account.id, "balance": -50.0}),
                ],
            }
        )
        self.assertEqual(self._balance(), 1000.0)
        move.action_post()
        self.assertEqual(self._balance(), 1050.0)
        self.fund.invalidate_recordset(["balance"])
        self.assertEqual(self.fund.balance, 1050.0)

    def test_funds_cannot_share_a_cash_account(self):
        with self.assertRaises(ValidationError):
            self.env["ebshel.petty.cash.fund"].create(
                {
                    "name": "Clone",
                    "code": "PCCL",
                    "journal_id": self.env["account.journal"]
                    .create(
                        {
                            "name": "Clone cash",
                            "code": "CLN",
                            "type": "cash",
                            "default_account_id": self.fund.account_id.id,
                        }
                    )
                    .id,
                }
            )

    def test_fund_with_entries_keeps_its_currency(self):
        eur = self.setup_other_currency("EUR")
        with self.assertRaises(UserError):
            self.fund.currency_id = eur

    def test_close_needs_the_cash_returned(self):
        with self.assertRaises(UserError):
            self.fund.action_close()
        self._fund_top_up(self.fund, 1000.0, kind="withdraw")
        self.assertEqual(self._balance(), 0.0)
        self.fund.action_close()
        self.assertEqual(self.fund.state, "closed")

    def test_withdrawal_cannot_exceed_cash(self):
        with self.assertRaises(UserError):
            self._fund_top_up(self.fund, 1500.0, kind="withdraw")

    def test_documents_need_an_open_fund(self):
        self.fund.action_suspend()
        voucher = self._voucher([(self.cat_travel, 10.0)])
        with self.assertRaises(UserError):
            voucher.action_submit()

    def test_low_balance_reminder_and_auto_replenishment(self):
        self.fund.auto_replenish = True
        voucher = self._approve_and_pay(self._voucher([(self.cat_travel, 850.0)]))
        self.assertEqual(voucher.state, "posted")
        self.fund.invalidate_recordset()
        self.assertTrue(self.fund.is_low)
        reminder = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_replenish")
        self.assertIn(reminder, self.fund.activity_ids.activity_type_id)

        self.env["ebshel.petty.cash.fund"]._cron_petty_cash_controls()
        request = self.env["ebshel.petty.cash.replenishment"].search(
            [("fund_id", "=", self.fund.id), ("state", "=", "draft")]
        )
        self.assertEqual(request.amount, 850.0)
        self.assertEqual(request.expense_ids, voucher)
        # a second run does not duplicate the request
        self.env["ebshel.petty.cash.fund"]._cron_petty_cash_controls()
        self.assertEqual(
            self.env["ebshel.petty.cash.replenishment"].search_count(
                [("fund_id", "=", self.fund.id), ("state", "=", "draft")]
            ),
            1,
        )

    def test_move_of_a_document_cannot_be_reset_to_draft(self):
        replenishment = self.env["ebshel.petty.cash.replenishment"].search(
            [("fund_id", "=", self.fund.id)]
        )
        with self.assertRaises(UserError):
            replenishment.move_id.button_draft()

    def test_dashboard_figures(self):
        self._approve_and_pay(self._voucher([(self.cat_travel, 100.0)]))
        approved = self._voucher([(self.cat_travel, 40.0)])
        approved.action_submit()
        approved.with_user(self.approver).action_approve()
        self.fund.invalidate_recordset()
        self.assertEqual(self.fund.balance, 900.0)
        self.assertEqual(self.fund.pending_amount, 40.0)
        self.assertEqual(self.fund.available_amount, 860.0)
        self.assertEqual(self.fund.to_pay_count, 1)
        self.assertEqual(self.fund.month_spent, 100.0)
        self.assertEqual(self.fund.replenish_amount, 100.0)

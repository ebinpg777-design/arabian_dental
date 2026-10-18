# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import PettyCashCommon


@tagged("post_install", "-at_install")
class TestPettyCashOperations(PettyCashCommon):

    # ------------------------------------------------------------------
    # Replenishment
    # ------------------------------------------------------------------
    def test_replenishment_claim_covers_the_paid_vouchers(self):
        self.company.petty_cash_replenish_approval = True
        paid = self._approve_and_pay(self._voucher([(self.cat_office, 115.0)]))
        self._approve_and_pay(self._voucher([(self.cat_travel, 85.0)]))
        self.fund.invalidate_recordset()
        self.assertEqual(self.fund.replenish_amount, 200.0)

        replenishment = (
            self.env["ebshel.petty.cash.replenishment"]
            .with_user(self.custodian)
            .create({"fund_id": self.fund.id, "amount": self.fund.replenish_amount})
        )
        self.assertEqual(replenishment.source_journal_id, self.bank_journal)
        replenishment.action_load_expenses()
        self.assertEqual(len(replenishment.expense_ids), 2)
        self.assertIn(paid, replenishment.expense_ids)
        self.assertEqual(replenishment.expense_total, 200.0)
        self.assertEqual(
            dict(replenishment._category_summary()),
            {"Office Supplies": 115.0, "Local Travel": 85.0},
        )
        replenishment.action_submit()
        self.assertEqual(replenishment.state, "to_approve")
        # approvers cannot approve replenishments, managers can
        with self.assertRaises(UserError):
            replenishment.with_user(self.approver).action_approve()
        replenishment.with_user(self.manager).action_approve()
        # and only managers post them
        with self.assertRaises(UserError):
            replenishment.with_user(self.custodian).action_post()
        replenishment.with_user(self.manager).action_post()
        self.assertEqual(self._balance(), 1000.0)

        # cancelling the top-up gives the vouchers back to the next claim
        replenishment.with_user(self.manager).action_cancel()
        self.assertEqual(self._balance(), 800.0)
        self.assertFalse(paid.replenishment_id)

    def test_manager_cannot_approve_own_replenishment(self):
        self.company.petty_cash_replenish_approval = True
        replenishment = (
            self.env["ebshel.petty.cash.replenishment"]
            .with_user(self.manager)
            .create({"fund_id": self.fund.id, "amount": 10.0})
        )
        replenishment.action_submit()
        with self.assertRaises(UserError):
            replenishment.action_approve()
        replenishment.with_user(self.manager_2).action_approve()
        self.assertEqual(replenishment.state, "approved")

    # ------------------------------------------------------------------
    # Cash count
    # ------------------------------------------------------------------
    def test_count_by_denominations_with_shortage(self):
        count = self.env["ebshel.petty.cash.count"].with_user(self.custodian).create(
            {"fund_id": self.fund.id}
        )
        Denomination = self.env["ebshel.petty.cash.denomination"]
        currency = self.fund.currency_id
        if not Denomination.search_count([("currency_id", "=", currency.id)]):
            Denomination.create(
                [{"currency_id": currency.id, "value": value} for value in (100.0, 50.0, 10.0, 5.0)]
            )
        count.action_load_denominations()
        self.assertTrue(count.line_ids)
        by_value = {line.value: line for line in count.line_ids}
        by_value[100.0].quantity = 9
        by_value[50.0].quantity = 1
        by_value[10.0].quantity = 4
        self.assertEqual(count.counted_amount, 990.0)
        self.assertEqual(count.expected_difference, -10.0)
        with self.assertRaises(UserError):
            count.action_confirm()  # a difference must be explained
        count.note = "Taxi fare without a receipt"
        count.action_confirm()
        self.assertEqual(count.state, "confirmed")
        self.assertEqual(count.book_balance, 1000.0)
        self.assertEqual(count.difference, -10.0)
        with self.assertRaises(UserError):
            count.with_user(self.custodian).action_post_difference()
        count.with_user(self.manager).action_post_difference()
        self.assertEqual(count.state, "done")
        loss_line = count.move_id.sudo().line_ids.filtered(lambda aml: aml.account_id != self.fund.account_id)
        self.assertEqual(loss_line.balance, 10.0)
        self.assertEqual(self._balance(), 990.0)

    def test_count_without_difference_is_done(self):
        count = self.env["ebshel.petty.cash.count"].with_user(self.custodian).create(
            {"fund_id": self.fund.id, "manual_amount": 1000.0}
        )
        count.action_confirm()
        self.assertEqual(count.state, "done")
        self.assertFalse(count.move_id)
        self.fund.invalidate_recordset()
        self.assertEqual(self.fund.last_count_date, self.today)

    def test_count_reminder(self):
        self.fund.count_interval_days = 7
        self.env["ebshel.petty.cash.fund"]._cron_petty_cash_controls()
        reminder = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_count")
        self.assertNotIn(reminder, self.fund.activity_ids.activity_type_id)
        self.env["ebshel.petty.cash.count"].with_user(self.custodian).create(
            # before the fund was topped up: nothing in the box, nothing in the books
            {"fund_id": self.fund.id, "manual_amount": 0.0, "date": self.today - timedelta(days=8)}
        ).action_confirm()
        self.fund.invalidate_recordset()
        self.env["ebshel.petty.cash.fund"]._cron_petty_cash_controls()
        self.assertIn(reminder, self.fund.activity_ids.activity_type_id)

    # ------------------------------------------------------------------
    # Transfer
    # ------------------------------------------------------------------
    def test_transfer_between_funds(self):
        branch = self._create_fund("Branch", "PCBR")
        transfer = self.env["ebshel.petty.cash.transfer"].with_user(self.manager).create(
            {"fund_id": self.fund.id, "dest_fund_id": branch.id, "amount": 250.0}
        )
        transfer.action_post()
        self.assertEqual(self._balance(), 750.0)
        self.assertEqual(self._balance(branch), 250.0)
        with self.assertRaises(UserError):
            self.env["ebshel.petty.cash.transfer"].with_user(self.manager).create(
                {"fund_id": self.fund.id, "dest_fund_id": branch.id, "amount": 800.0}
            ).action_post()
        transfer.action_cancel()
        self.assertEqual(self._balance(), 1000.0)
        self.assertEqual(self._balance(branch), 0.0)

    def test_transfer_needs_two_funds_of_one_currency(self):
        with self.assertRaises(ValidationError):
            self.env["ebshel.petty.cash.transfer"].create(
                {"fund_id": self.fund.id, "dest_fund_id": self.fund.id, "amount": 1.0}
            )
        eur = self.setup_other_currency("EUR")
        euro_fund = self._create_fund("Euro", "PCEU", currency_id=eur.id)
        with self.assertRaises(ValidationError):
            self.env["ebshel.petty.cash.transfer"].create(
                {"fund_id": self.fund.id, "dest_fund_id": euro_fund.id, "amount": 1.0}
            )

    # ------------------------------------------------------------------
    # Statement and printed documents
    # ------------------------------------------------------------------
    def test_statement(self):
        self._approve_and_pay(self._voucher([(self.cat_office, 115.0)]))
        self._approve_and_pay(self._voucher([(self.cat_travel, 35.0)]))
        wizard = self.env["ebshel.petty.cash.statement.wizard"].create(
            {
                "fund_ids": [Command.set(self.fund.ids)],
                "date_from": self.today,
                "date_to": self.today,
            }
        )
        (data,) = wizard._statement_data()
        self.assertEqual(data["opening"], 0.0)
        self.assertEqual(data["total_in"], 1000.0)
        self.assertEqual(data["total_out"], 150.0)
        self.assertEqual(data["closing"], 850.0)
        self.assertEqual(data["rows"][-1]["balance"], 850.0)
        wizard_later = self.env["ebshel.petty.cash.statement.wizard"].create(
            {
                "fund_ids": [Command.set(self.fund.ids)],
                "date_from": self.today + timedelta(days=1),
                "date_to": self.today + timedelta(days=2),
            }
        )
        (later,) = wizard_later._statement_data()
        self.assertEqual(later["opening"], 850.0)
        self.assertFalse(later["rows"])

    def test_reports_render(self):
        voucher = self._approve_and_pay(self._voucher([(self.cat_office, 115.0)]))
        advance = self.env["ebshel.petty.cash.advance"].create(
            {"fund_id": self.fund.id, "holder_id": self.member.partner_id.id, "purpose": "Trip", "amount": 10.0}
        )
        replenishment = self.env["ebshel.petty.cash.replenishment"].search([("fund_id", "=", self.fund.id)], limit=1)
        count = self.env["ebshel.petty.cash.count"].create({"fund_id": self.fund.id, "manual_amount": 885.0})
        wizard = self.env["ebshel.petty.cash.statement.wizard"].create(
            {"fund_ids": [Command.set(self.fund.ids)], "date_from": self.today, "date_to": self.today}
        )
        Report = self.env["ir.actions.report"]
        for xmlid, records, expected in (
            ("ebshel_petty_cash.action_report_petty_cash_expense", voucher, voucher.name),
            ("ebshel_petty_cash.action_report_petty_cash_advance", advance, advance.name),
            ("ebshel_petty_cash.action_report_petty_cash_replenishment", replenishment, replenishment.name),
            ("ebshel_petty_cash.action_report_petty_cash_count", count, count.name),
            ("ebshel_petty_cash.action_report_petty_cash_statement", wizard, "Fund Statement"),
        ):
            html = Report._render_qweb_html(xmlid, records.ids)[0]
            self.assertIn(expected.encode(), html, xmlid)

# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import PettyCashCommon


@tagged("post_install", "-at_install")
class TestPettyCashExpense(PettyCashCommon):

    # ------------------------------------------------------------------
    # Amounts and entries
    # ------------------------------------------------------------------
    def test_tax_included_receipt(self):
        voucher = self._voucher([(self.cat_office, 115.0)])
        self.assertRecordValues(
            voucher, [{"amount_untaxed": 100.0, "amount_tax": 15.0, "amount_total": 115.0}]
        )
        self._approve_and_pay(voucher)
        self.assertEqual(voucher.state, "posted")
        move = voucher.move_id.sudo()  # the requester has no accounting rights
        self.assertEqual(move.state, "posted")
        expense_line = move.line_ids.filtered(lambda line: line.tax_ids)
        tax_line = move.line_ids.filtered("tax_repartition_line_id")
        cash_line = move.line_ids.filtered(lambda line: line.account_id == self.fund.account_id)
        self.assertEqual(expense_line.balance, 100.0)
        self.assertEqual(expense_line.account_id, self.expense_account)
        self.assertEqual(tax_line.balance, 15.0)
        self.assertEqual(tax_line.tax_line_id, self.tax_15)
        self.assertEqual(cash_line.balance, -115.0)
        self.assertEqual(self._balance(), 885.0)

    def test_tax_excluded_amounts(self):
        voucher = self._voucher([(self.cat_office, 100.0)], price_mode="excluded")
        self.assertEqual(voucher.amount_total, 115.0)
        self._approve_and_pay(voucher)
        self.assertEqual(self._balance(), 885.0)

    def test_rounding_matches_the_entry(self):
        """Global rounding of several tax-included lines: the voucher total is
        what leaves the cash box, cent for cent."""
        voucher = self._voucher(
            [(self.cat_office, 10.0), (self.cat_office, 10.0), (self.cat_office, 10.0), (self.cat_travel, 3.33)]
        )
        self.assertEqual(voucher.amount_total, 33.33)
        self._approve_and_pay(voucher)
        cash_line = voucher.move_id.sudo().line_ids.filtered(lambda line: line.account_id == self.fund.account_id)
        self.assertAlmostEqual(cash_line.balance, -33.33)
        self.assertAlmostEqual(
            sum(voucher.move_id.sudo().line_ids.filtered("tax_repartition_line_id").mapped("balance")),
            voucher.amount_tax,
        )

    def test_foreign_currency_fund(self):
        eur = self.setup_other_currency("EUR", rates=[("2000-01-01", 2.0)])
        fund = self._create_fund("Paris", "PCPA", currency_id=eur.id)
        self.assertEqual(fund.journal_id.currency_id, eur)
        self._fund_top_up(fund, 500.0)
        self.assertEqual(self._balance(fund), 500.0)
        voucher = self._approve_and_pay(self._voucher([(self.cat_office, 115.0)], fund=fund))
        cash_line = voucher.move_id.sudo().line_ids.filtered(lambda line: line.account_id == fund.account_id)
        self.assertEqual(cash_line.currency_id, eur)
        self.assertAlmostEqual(cash_line.amount_currency, -115.0)
        self.assertAlmostEqual(cash_line.balance, -57.5)
        self.assertEqual(self._balance(fund), 385.0)
        self.assertAlmostEqual(voucher.amount_total_company, 57.5)

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------
    def test_auto_approval_under_the_fund_limit(self):
        self.fund.auto_approve_limit = 50.0
        small = self._voucher([(self.cat_travel, 50.0)])
        small.action_submit()
        self.assertEqual(small.state, "approved")
        big = self._voucher([(self.cat_travel, 50.01)])
        big.action_submit()
        self.assertEqual(big.state, "to_approve")
        approval = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_approval")
        self.assertFalse(big.activity_ids.filtered(lambda a: a.activity_type_id == approval))

    def test_approvers_get_an_activity(self):
        self.fund.approver_ids = self.approver
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        self.assertEqual(voucher.activity_ids.user_id, self.approver)
        voucher.with_user(self.approver).action_approve()
        self.assertFalse(voucher.activity_ids)

    def test_no_self_approval(self):
        voucher = self._voucher([(self.cat_travel, 80.0)], user=self.approver)
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_approve()
        self.company.petty_cash_self_approval = True
        voucher.with_user(self.approver).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_users_cannot_approve(self):
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian).action_approve()

    def test_named_approvers_only(self):
        other_approver = self.approver.copy({"login": "pc_approver_2"})
        self.fund.approver_ids = other_approver
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_approve()
        voucher.with_user(other_approver).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_second_approval_above_the_company_threshold(self):
        self.company.write(
            {"petty_cash_double_validation": True, "petty_cash_double_validation_amount": 500.0}
        )
        below = self._voucher([(self.cat_travel, 499.0)])
        below.action_submit()
        below.with_user(self.approver).action_approve()
        self.assertEqual(below.state, "approved")

        above = self._voucher([(self.cat_travel, 500.0)])
        above.action_submit()
        above.with_user(self.approver).action_approve()
        self.assertEqual(above.state, "to_approve")
        self.assertEqual(above.first_approver_id, self.approver)
        # the second approval needs another person, who is a manager
        with self.assertRaises(UserError):
            above.with_user(self.approver).action_approve()
        above.with_user(self.manager).action_approve()
        self.assertEqual(above.state, "approved")
        self.assertEqual(above.approver_id, self.manager)

    def test_refuse_and_resubmit(self):
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        wizard = (
            self.env["ebshel.petty.cash.refuse.wizard"]
            .with_user(self.approver)
            .with_context(active_model=voucher._name, active_ids=voucher.ids)
            .create({"reason": "No receipt"})
        )
        wizard.action_refuse()
        self.assertEqual(voucher.state, "refused")
        self.assertEqual(voucher.refuse_reason, "No receipt")
        voucher.action_draft()
        self.assertEqual(voucher.state, "draft")

    def test_pay_on_approval(self):
        self.fund.post_on_approval = True
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        self.assertEqual(voucher.state, "posted")
        self.assertEqual(self._balance(), 920.0)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def test_limit_per_voucher(self):
        self.fund.max_expense_amount = 100.0
        with self.assertRaises(UserError):
            self._voucher([(self.cat_travel, 60.0), (self.cat_travel, 41.0)]).action_submit()

    def test_limit_per_category_line(self):
        self.cat_travel.max_amount = 50.0
        with self.assertRaises(UserError):
            self._voucher([(self.cat_travel, 51.0)]).action_submit()
        self._voucher([(self.cat_travel, 50.0)]).action_submit()

    def test_category_restricted_to_fund(self):
        self.fund.category_ids = self.cat_office
        with self.assertRaises(UserError):
            self._voucher([(self.cat_travel, 10.0)]).action_submit()

    def test_receipt_required_above_threshold(self):
        self.fund.write({"require_receipt": True, "receipt_required_amount": 20.0})
        self._voucher([(self.cat_travel, 20.0)]).action_submit()
        voucher = self._voucher([(self.cat_travel, 20.01)])
        with self.assertRaises(UserError):
            voucher.action_submit()
        self._attach_receipt(voucher)
        voucher.action_submit()
        self.assertEqual(voucher.state, "to_approve")

    def test_category_requiring_receipt_and_payee(self):
        self.cat_travel.write({"receipt_required": True, "partner_required": True})
        voucher = self._voucher([(self.cat_travel, 5.0)])
        self._attach_receipt(voucher)
        with self.assertRaises(UserError):
            voucher.action_submit()
        voucher.partner_id = self.partner_a
        voucher.action_submit()

    def test_cannot_pay_more_than_the_cash(self):
        voucher = self._voucher([(self.cat_travel, 1000.01)])
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian).action_post()
        self.fund.allow_negative = True
        voucher.with_user(self.custodian).action_post()
        self.assertAlmostEqual(self._balance(), -0.01)

    def test_only_the_custodian_pays(self):
        voucher = self._voucher([(self.cat_travel, 10.0)])
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        self.assertFalse(voucher.with_user(self.member).can_pay)
        self.assertTrue(voucher.with_user(self.custodian).can_pay)
        with self.assertRaises(UserError):
            voucher.with_user(self.member).action_post()
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_post()
        voucher.with_user(self.manager).action_post()
        self.assertEqual(voucher.state, "posted")

    def test_approved_voucher_is_locked(self):
        voucher = self._voucher([(self.cat_travel, 10.0)])
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            voucher.line_ids.with_user(self.member).amount = 99.0
        with self.assertRaises(UserError):
            voucher.with_user(self.member).write(
                {"line_ids": [Command.create({"category_id": self.cat_travel.id, "amount": 1.0})]}
            )

    def test_duplicate_detection(self):
        first = self._voucher([(self.cat_travel, 42.0)], reference="R-1")
        second = self._voucher([(self.cat_travel, 42.0)], reference="r-1")
        self.assertTrue(second.is_duplicate)
        self.assertEqual(second.duplicate_ids, first)
        third = self._voucher([(self.cat_travel, 42.5)], reference="R-1")
        self.assertFalse(third.is_duplicate)

    def test_cancel_paid_voucher_reverses_it(self):
        voucher = self._approve_and_pay(self._voucher([(self.cat_office, 115.0)]))
        self.assertEqual(self._balance(), 885.0)
        voucher.with_user(self.custodian).action_cancel()
        self.assertEqual(voucher.state, "cancelled")
        self.assertEqual(self._balance(), 1000.0)
        self.assertTrue(voucher.move_id.sudo().reversal_move_ids)

    # ------------------------------------------------------------------
    # Vendor bills
    # ------------------------------------------------------------------
    def test_pay_a_vendor_bill_from_the_cash_box(self):
        bill = self.init_invoice("in_invoice", partner=self.partner_a, amounts=[200.0], post=True)
        self.assertEqual(bill.currency_id, self.fund.currency_id)
        voucher = self.env["ebshel.petty.cash.expense"].create(
            {
                "fund_id": self.fund.id,
                "description": "Supplier paid in cash",
                "partner_id": self.partner_a.id,
                "line_ids": [Command.create({"bill_id": bill.id, "amount": bill.amount_residual})],
            }
        )
        self.assertFalse(voucher.line_ids.tax_ids)
        self.assertEqual(voucher.amount_total, bill.amount_residual)
        self._approve_and_pay(voucher)
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertAlmostEqual(self._balance(), 1000.0 - bill.amount_total)
        # cancelling the voucher re-opens the bill
        voucher.action_cancel()
        self.assertEqual(bill.payment_state, "not_paid")

    def test_bill_payment_cannot_exceed_the_residual(self):
        bill = self.init_invoice("in_invoice", partner=self.partner_a, amounts=[100.0], post=True)
        voucher = self.env["ebshel.petty.cash.expense"].create(
            {
                "fund_id": self.fund.id,
                "description": "Too much",
                "partner_id": self.partner_a.id,
                "line_ids": [Command.create({"bill_id": bill.id, "amount": bill.amount_residual + 1})],
            }
        )
        with self.assertRaises(UserError):
            voucher.action_submit()

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    def test_record_rules(self):
        mine = self._voucher([(self.cat_travel, 10.0)])
        theirs = self._voucher([(self.cat_travel, 10.0)], user=self.custodian)
        Expense = self.env["ebshel.petty.cash.expense"]
        self.assertEqual(Expense.with_user(self.member).search([("id", "in", (mine | theirs).ids)]), mine)
        # the custodian sees every voucher of the fund
        self.assertEqual(
            Expense.with_user(self.custodian).search([("id", "in", (mine | theirs).ids)]), mine | theirs
        )
        # a user of another fund sees neither the fund nor its vouchers
        self.assertFalse(Expense.with_user(self.outsider).search([("id", "in", (mine | theirs).ids)]))
        with self.assertRaises(AccessError):
            self.fund.with_user(self.outsider).read(["name"])
        with self.assertRaises(AccessError):
            self.fund.with_user(self.member).write({"float_amount": 5.0})

    def test_custodian_without_accounting_rights_pays(self):
        self.assertFalse(self.custodian.has_group("account.group_account_invoice"))
        self.assertFalse(self.custodian.has_group("account.group_account_readonly"))
        voucher = self._approve_and_pay(self._voucher([(self.cat_office, 23.0)]))
        self.assertEqual(voucher.state, "posted")
        voucher.with_user(self.custodian).action_cancel()
        self.assertEqual(voucher.state, "cancelled")

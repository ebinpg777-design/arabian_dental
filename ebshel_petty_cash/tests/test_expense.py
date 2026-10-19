# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
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
    def test_no_matching_level_approves_on_submission(self):
        self.level_approver.min_amount = 50.01
        small = self._voucher([(self.cat_travel, 50.0)])
        small.action_submit()
        self.assertEqual(small.state, "approved")
        self.assertFalse(small.approval_step_ids)
        big = self._voucher([(self.cat_travel, 50.01)])
        big.action_submit()
        self.assertEqual(big.state, "to_approve")
        self.assertEqual(big.approval_step_ids.level_id, self.level_approver)

    def test_approvers_get_an_activity(self):
        self.level_approver.write({"approver_type": "users", "user_ids": [Command.set(self.approver.ids)]})
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        self.assertEqual(voucher.activity_ids.user_id, self.approver)
        self.assertEqual(voucher.pending_approver_ids, self.approver)
        voucher.with_user(self.approver).action_approve()
        self.assertFalse(voucher.activity_ids)
        self.assertEqual(voucher.approval_step_ids.state, "approved")
        self.assertEqual(voucher.approval_step_ids.done_by_id, self.approver)

    def test_no_self_approval(self):
        voucher = self._voucher([(self.cat_travel, 80.0)], user=self.approver)
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_approve()
        voucher.action_draft()
        self.company.petty_cash_self_approval = True
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_users_cannot_approve(self):
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.custodian).action_approve()

    def test_named_approvers_only(self):
        other_approver = self.approver.copy({"login": "pc_approver_2"})
        self.level_approver.write({"approver_type": "users", "user_ids": [Command.set(other_approver.ids)]})
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_approve()
        voucher.with_user(other_approver).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_level_approvers_need_the_approver_right(self):
        with self.assertRaises(ValidationError):
            self._level("Custodian check", approver_type="users", user_ids=[Command.set(self.member.ids)])

    def test_multi_level_chain_by_amount(self):
        self.level_approver.write({"approver_type": "users", "user_ids": [Command.set(self.approver.ids)]})
        finance = self._level(
            "Finance", sequence=20, min_amount=500.0, approver_type="users",
            user_ids=[Command.set(self.manager.ids)],
        )
        director = self._level(
            "Director", sequence=30, min_amount=800.0, approver_type="managers", approval_mode="all",
        )
        below = self._voucher([(self.cat_travel, 499.0)])
        below.action_submit()
        self.assertEqual(below.approval_step_ids.level_id, self.level_approver)

        above = self._voucher([(self.cat_travel, 900.0)])
        above.action_submit()
        steps = above.approval_step_ids.sorted("sequence")
        self.assertEqual(steps.level_id, self.level_approver | finance | director)
        self.assertEqual(steps.mapped("state"), ["pending", "waiting", "waiting"])
        self.assertEqual(above.approval_progress, "0 / 3")
        # the finance manager cannot jump the queue
        with self.assertRaises(UserError):
            above.with_user(self.manager).action_approve()
        above.with_user(self.approver).action_approve()
        self.assertEqual(above.current_step_id.level_id, finance)
        self.assertEqual(above.pending_approver_ids, self.manager)
        above.with_user(self.manager).action_approve()
        # every administrator must approve the director level - but the finance
        # manager already approved a level: segregation of duties leaves manager_2
        self.assertEqual(above.current_step_id.level_id, director)
        self.assertNotIn(self.manager, above.current_step_id.approver_ids)
        self.assertIn(self.manager_2, above.current_step_id.approver_ids)
        with self.assertRaises(UserError):
            above.with_user(self.manager).action_approve()
        for user in above.current_step_id.approver_ids:
            above.with_user(user).action_approve()
        self.assertEqual(above.state, "approved")
        self.assertEqual(above.approval_progress, "3 / 3")

    def test_all_approvers_mode(self):
        other_approver = self.approver.copy({"login": "pc_approver_2"})
        self.level_approver.write(
            {
                "approver_type": "users",
                "user_ids": [Command.set((self.approver | other_approver).ids)],
                "approval_mode": "all",
            }
        )
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        voucher.with_user(self.approver).action_approve()
        self.assertEqual(voucher.state, "to_approve")
        self.assertEqual(voucher.pending_approver_ids, other_approver)
        with self.assertRaises(UserError):
            voucher.with_user(self.approver).action_approve()
        voucher.with_user(other_approver).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_category_level(self):
        entertainment = self._level(
            "Entertainment", sequence=20, approver_type="managers",
            category_ids=[Command.set(self.cat_office.ids)], apply_on_advance=False,
        )
        travel = self._voucher([(self.cat_travel, 30.0)])
        travel.action_submit()
        self.assertNotIn(entertainment, travel.approval_step_ids.level_id)
        office = self._voucher([(self.cat_office, 30.0)])
        office.action_submit()
        self.assertIn(entertainment, office.approval_step_ids.level_id)

    def test_fund_level(self):
        branch = self._create_fund("Branch", "PCBR")
        self._level("Branch manager", sequence=20, approver_type="custodian", fund_ids=[Command.set(branch.ids)])
        self._fund_top_up(branch, 100.0)
        here = self._voucher([(self.cat_travel, 30.0)])
        here.action_submit()
        self.assertEqual(len(here.approval_step_ids), 1)
        there = self._voucher([(self.cat_travel, 30.0)], fund=branch)
        there.action_submit()
        self.assertEqual(len(there.approval_step_ids), 2)
        self._approve_all(there)
        self.assertEqual(there.approval_step_ids[-1].done_by_id, self.custodian)

    def test_delegation(self):
        self.level_approver.write({"approver_type": "users", "user_ids": [Command.set(self.approver.ids)]})
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        with self.assertRaises(UserError):
            voucher.with_user(self.manager).action_approve()
        self.env["ebshel.petty.cash.delegation"].with_user(self.approver).create(
            {"delegate_id": self.manager.id, "date_to": self.today, "note": "Leave"}
        )
        Step = self.env["ebshel.petty.cash.approval.step"].with_user(self.manager)
        self.assertIn(voucher.approval_step_ids, Step.search([("is_my_approval", "=", True)]))
        voucher.with_user(self.manager).action_approve()
        self.assertEqual(voucher.state, "approved")
        self.assertEqual(voucher.approval_step_ids.approved_user_ids, self.approver)
        self.assertEqual(voucher.approval_step_ids.done_by_id, self.manager)

    def test_escalation(self):
        self.level_approver.write(
            {
                "approver_type": "users",
                "user_ids": [Command.set(self.approver.ids)],
                "escalation_days": 2,
                "escalation_user_id": self.manager.id,
            }
        )
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        step = voucher.approval_step_ids
        self.assertEqual(step.deadline, self.today + timedelta(days=2))
        Step = self.env["ebshel.petty.cash.approval.step"]
        Step._cron_escalate()
        self.assertFalse(step.escalated)
        step.sudo().deadline = self.today - timedelta(days=1)
        Step._cron_escalate()
        self.assertTrue(step.escalated)
        self.assertIn(self.manager, step.approver_ids)
        voucher.with_user(self.manager).action_approve()
        self.assertEqual(voucher.state, "approved")

    def test_approval_inbox(self):
        voucher = self._voucher([(self.cat_travel, 80.0)])
        voucher.action_submit()
        Step = self.env["ebshel.petty.cash.approval.step"]
        mine = Step.with_user(self.approver).search([("is_my_approval", "=", True)])
        self.assertEqual(mine, voucher.approval_step_ids)
        self.assertFalse(Step.with_user(self.member).search([("is_my_approval", "=", True)]))
        self.assertEqual(
            self.env["ebshel.petty.cash.expense"].with_user(self.approver).search([("is_my_approval", "=", True)]),
            voucher,
        )
        self.assertTrue(mine.with_user(self.approver).can_approve)
        mine.with_user(self.approver).action_approve()
        self.assertEqual(voucher.state, "approved")
        self.assertGreaterEqual(mine.duration_hours, 0.0)

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
        self.assertEqual(voucher.approval_step_ids.state, "refused")
        voucher.action_draft()
        self.assertEqual(voucher.state, "draft")
        self.assertFalse(voucher.approval_step_ids)

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
        self.assertEqual(self._balance(), 1000.0)

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

    # ------------------------------------------------------------------
    # Budget and risk
    # ------------------------------------------------------------------
    def test_monthly_budget_blocks(self):
        self.fund.write({"monthly_budget": 300.0, "budget_policy": "block"})
        self._approve_and_pay(self._voucher([(self.cat_travel, 200.0)]))
        self.fund.invalidate_recordset()
        self.assertAlmostEqual(self.fund.budget_used_pct, 200.0 / 3.0)
        with self.assertRaises(UserError):
            self._voucher([(self.cat_travel, 101.0)]).action_submit()
        self._voucher([(self.cat_travel, 100.0)]).action_submit()

    def test_monthly_budget_warns(self):
        self.fund.write({"monthly_budget": 100.0, "budget_policy": "warn"})
        voucher = self._voucher([(self.cat_travel, 120.0)])
        voucher.action_submit()
        self.assertEqual(voucher.state, "to_approve")
        self.assertIn("budget", voucher.risk_flags)
        self.fund.invalidate_recordset()
        self.assertEqual(self.fund.attention_count, 1)

    def test_monthly_limit_per_person(self):
        self.fund.user_monthly_limit = 100.0
        self._voucher([(self.cat_travel, 60.0)]).action_submit()
        with self.assertRaises(UserError):
            self._voucher([(self.cat_travel, 41.0)]).action_submit()
        # another person has their own allowance
        self._voucher([(self.cat_travel, 41.0)], user=self.custodian).action_submit()

    def test_risk_scoring_and_review(self):
        self.company.petty_cash_risk_review = True
        self._level("Finance", sequence=20, min_amount=500.0, approver_type="users",
                    user_ids=[Command.set(self.approver.ids)])
        first = self._voucher([(self.cat_travel, 300.0)], reference="R-9")
        first.action_submit()
        # same receipt again, no receipt attached, just under the 500 threshold
        # with the first one the same day: a split
        second = self._voucher([(self.cat_travel, 300.0)], reference="R-9")
        second.action_submit()
        self.assertEqual(second.risk_level, "high")
        self.assertIn("duplicate", second.risk_flags)
        self.assertIn("split", second.risk_flags.lower())
        self.assertIn("No receipt", second.risk_flags)
        self.assertTrue(second.risk_flags_html)
        # an administrators' review is added to the chain
        self.assertEqual(second.approval_step_ids.sorted("sequence")[-1].name, "Risk Review")
        self._approve_all(second)
        self.assertEqual(second.state, "approved")
        self.assertTrue(second.approval_step_ids[-1].done_by_id.has_group("ebshel_petty_cash.group_petty_cash_manager"))

    def test_low_risk_voucher(self):
        voucher = self._voucher([(self.cat_travel, 37.0)], date=self.today - timedelta(days=self.today.weekday()))
        self._attach_receipt(voucher)
        voucher.action_submit()
        self.assertEqual(voucher.risk_level, "low")
        self.assertEqual(voucher.risk_score, 0)

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

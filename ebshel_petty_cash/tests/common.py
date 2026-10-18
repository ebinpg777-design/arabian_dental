# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.

from odoo import Command, fields
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class PettyCashCommon(AccountTestInvoicingCommon):
    """A company with one open fund (float 1000, funded), its custodian, a
    member, an approver and two managers; two expense categories."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.today = fields.Date.context_today(cls.env.user)
        cls.company.write(
            {
                "petty_cash_replenish_approval": False,
                "petty_cash_duplicate_days": 30,
                "tax_calculation_rounding_method": "round_globally",
            }
        )
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.expense_account = cls.company_data["default_account_expense"]
        cls.tax_15 = cls.env["account.tax"].create(
            {
                "name": "Purchase 15%",
                "amount": 15.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": cls.company.id,
            }
        )

        def user(login, groups):
            return new_test_user(
                cls.env,
                login=login,
                groups=groups,
                company_id=cls.company.id,
                company_ids=[Command.set(cls.company.ids)],
            )

        cls.custodian = user("pc_custodian", "ebshel_petty_cash.group_petty_cash_user")
        cls.member = user("pc_member", "ebshel_petty_cash.group_petty_cash_user")
        cls.outsider = user("pc_outsider", "ebshel_petty_cash.group_petty_cash_user")
        cls.approver = user("pc_approver", "ebshel_petty_cash.group_petty_cash_approver")
        cls.manager = user("pc_manager", "ebshel_petty_cash.group_petty_cash_manager")
        cls.manager_2 = user("pc_manager_2", "ebshel_petty_cash.group_petty_cash_manager")

        Category = cls.env["ebshel.petty.cash.category"]
        cls.cat_office = Category.create(
            {
                "name": "Office Supplies",
                "account_id": cls.expense_account.id,
                "tax_ids": [Command.set(cls.tax_15.ids)],
            }
        )
        cls.cat_travel = Category.create(
            {"name": "Local Travel", "account_id": cls.expense_account.id}
        )

        cls.fund = cls._create_fund("Head Office", "PCHO")
        cls._fund_top_up(cls.fund, 1000.0)

    @classmethod
    def _create_fund(cls, name, code, **vals):
        fund = cls.env["ebshel.petty.cash.fund"].create(
            {
                "name": name,
                "code": code,
                "company_id": cls.company.id,
                "custodian_id": cls.custodian.id,
                "user_ids": [Command.set(cls.member.ids)],
                "float_amount": 1000.0,
                "min_balance": 200.0,
                "source_journal_id": cls.bank_journal.id,
                "require_receipt": False,
                **vals,
            }
        )
        fund.action_open()
        return fund

    @classmethod
    def _fund_top_up(cls, fund, amount, kind="replenish"):
        replenishment = (
            cls.env["ebshel.petty.cash.replenishment"]
            .with_user(cls.manager)
            .create({"fund_id": fund.id, "amount": amount, "kind": kind})
        )
        replenishment.action_submit()
        replenishment.action_post()
        return replenishment

    def _voucher(self, lines, user=None, **vals):
        """Voucher of ``lines`` = [(category, amount)] raised by ``user``."""
        return (
            self.env["ebshel.petty.cash.expense"]
            .with_user(user or self.member)
            .create(
                {
                    "fund_id": vals.pop("fund", self.fund).id,
                    "description": vals.pop("description", "Petty spending"),
                    "line_ids": [
                        Command.create({"category_id": category.id, "amount": amount})
                        for category, amount in lines
                    ],
                    **vals,
                }
            )
        )

    def _attach_receipt(self, record):
        return self.env["ir.attachment"].create(
            {
                "name": "receipt.txt",
                "raw": b"receipt",
                "res_model": record._name,
                "res_id": record.id,
            }
        )

    def _approve_and_pay(self, voucher):
        voucher.with_user(voucher.requested_by_id).action_submit()
        if voucher.state == "to_approve":
            voucher.with_user(self.approver).action_approve()
        if voucher.state == "to_approve":
            voucher.with_user(self.manager).action_approve()
        if voucher.state == "approved":
            voucher.with_user(self.custodian).action_post()
        return voucher

    def _balance(self, fund=None):
        return (fund or self.fund)._get_balance()

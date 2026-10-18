# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import AccessError

from .common import PettyCashCommon


class TestPettyCashAccess(PettyCashCommon):
    """The ir.access permissions and restrictions, role by role. Searches go
    to the database, so the cache of the records created as admin cannot
    answer for the user."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # a fund the member and the approver are neither custodian nor member of
        cls.branch = cls._create_fund("Branch", "PCBR", custodian_id=cls.outsider.id, user_ids=[])

    def _visible(self, user, records):
        return records.with_user(user).search([("id", "in", records.ids)])

    def test_funds_by_role(self):
        funds = self.fund | self.branch
        self.assertEqual(self._visible(self.member, funds), self.fund)
        self.assertEqual(self._visible(self.custodian, funds), self.fund)
        self.assertEqual(self._visible(self.outsider, funds), self.branch)
        self.assertEqual(self._visible(self.approver, funds), funds)
        self.assertEqual(self._visible(self.manager, funds), funds)
        with self.assertRaises(AccessError):
            self.fund.with_user(self.approver).write({"min_balance": 100.0})
        self.fund.with_user(self.manager).write({"min_balance": 100.0})

    def test_approvers_see_every_voucher(self):
        mine = self._voucher([(self.cat_travel, 10.0)])
        elsewhere = self._voucher([(self.cat_travel, 10.0)], user=self.outsider, fund=self.branch)
        vouchers = mine | elsewhere
        self.assertEqual(self._visible(self.member, vouchers), mine)
        self.assertEqual(self._visible(self.outsider, vouchers), elsewhere)
        self.assertEqual(self._visible(self.approver, vouchers), vouchers)
        self.assertEqual(self._visible(self.approver, vouchers.line_ids), vouchers.line_ids)
        self.assertEqual(self._visible(self.member, vouchers.line_ids), mine.line_ids)

    def test_master_data_is_read_only_for_users(self):
        categories = self.cat_office | self.cat_travel
        self.assertEqual(self._visible(self.member, categories), categories)
        with self.assertRaises(AccessError):
            self.cat_office.with_user(self.member).write({"name": "Stationery"})
        with self.assertRaises(AccessError):
            self.cat_office.with_user(self.approver).write({"name": "Stationery"})
        self.cat_office.with_user(self.manager).write({"name": "Stationery"})
        denomination = self.env["ebshel.petty.cash.denomination"].search([], limit=1)
        self.assertTrue(denomination.with_user(self.member).read(["value"]))
        with self.assertRaises(AccessError):
            denomination.with_user(self.member).write({"active": False})

    def test_transfers_are_read_by_the_custodians(self):
        transfer = self.env["ebshel.petty.cash.transfer"].with_user(self.manager).create(
            {"fund_id": self.fund.id, "dest_fund_id": self.branch.id, "amount": 50.0}
        )
        self.assertEqual(self._visible(self.custodian, transfer), transfer)
        self.assertEqual(self._visible(self.outsider, transfer), transfer)
        self.assertEqual(self._visible(self.approver, transfer), transfer)
        self.assertFalse(self._visible(self.member, transfer))
        with self.assertRaises(AccessError):
            transfer.with_user(self.custodian).write({"amount": 10.0})
        with self.assertRaises(AccessError):
            self.env["ebshel.petty.cash.transfer"].with_user(self.custodian).create(
                {"fund_id": self.fund.id, "dest_fund_id": self.branch.id, "amount": 5.0}
            )

    def test_other_companies_stay_hidden(self):
        company_2 = self.setup_other_company()
        category = self.env["ebshel.petty.cash.category"].create(
            {
                "name": "Other company",
                "company_id": company_2["company"].id,
                "account_id": company_2["default_account_expense"].id,
            }
        )
        self.assertFalse(self._visible(self.manager, category))
        self.assertFalse(self._visible(self.approver, category))

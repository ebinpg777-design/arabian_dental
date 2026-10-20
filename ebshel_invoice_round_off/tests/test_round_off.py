# Part of ebshel_invoice_round_off. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRoundOff(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.nearest_unit = cls.env["account.cash.rounding"].create(
            {
                "name": "Nearest unit",
                "rounding": 1.0,
                "rounding_method": "HALF-UP",
                "strategy": "add_invoice_line",
                "profit_account_id": cls.company_data["default_account_revenue"].id,
                "loss_account_id": cls.company_data["default_account_expense"].id,
                "company_id": cls.company.id,
            }
        )
        # the accounting test base ships two methods of its own (0.05, one per strategy)
        cls.company.invoice_round_off_id = cls.nearest_unit

    def _invoice(self, amount, move_type="out_invoice", partner=None):
        return self.init_invoice(
            move_type, partner=partner or self.partner_a, invoice_date="2026-01-10",
            amounts=[amount], taxes=self.env["account.tax"],
        )

    def _rounding_line(self, invoice):
        return invoice.line_ids.filtered(lambda line: line.display_type == "rounding")

    def _assert_rounding(self, invoice, amount):
        """One rounding line for `amount` (as the customer sees it) on a rounding account."""
        line = self._rounding_line(invoice)
        self.assertEqual(len(line), 1)
        # the line's own sign is the entry's: a reduction of the total is a debit
        self.assertEqual(-line.amount_currency, amount)
        self.assertIn(line.account_id, self.nearest_unit.profit_account_id | self.nearest_unit.loss_account_id)

    def test_round_off_and_remove(self):
        invoice = self._invoice(650.01)
        self.assertEqual(invoice.amount_total, 650.01)
        self.assertFalse(invoice.invoice_cash_rounding_id)

        invoice.action_round_off()
        self.assertEqual(invoice.invoice_cash_rounding_id, self.nearest_unit)
        self.assertEqual(invoice.amount_total, 650.0)
        self._assert_rounding(invoice, -0.01)
        self.assertTrue(invoice.round_off_applied)

        invoice.action_remove_round_off()
        self.assertFalse(invoice.invoice_cash_rounding_id)
        self.assertFalse(self._rounding_line(invoice))
        self.assertEqual(invoice.amount_total, 650.01)

    def test_rounds_up_and_posts(self):
        invoice = self._invoice(399.99)
        invoice.action_round_off()
        self.assertEqual(invoice.amount_total, 400.0)
        self._assert_rounding(invoice, 0.01)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.amount_residual, 400.0)

    def test_bills_too(self):
        bill = self._invoice(120.49, move_type="in_invoice", partner=self.partner_b)
        bill.action_round_off()
        self.assertEqual(bill.amount_total, 120.0)

    def test_the_companys_method_is_used(self):
        other = self.nearest_unit.copy({"name": "Nearest ten", "rounding": 10.0})
        self.company.invoice_round_off_id = other
        invoice = self._invoice(646.0)
        invoice.action_round_off()
        self.assertEqual(invoice.invoice_cash_rounding_id, other)
        self.assertEqual(invoice.amount_total, 650.0)

    def test_without_a_company_method_the_first_one_serves(self):
        self.company.invoice_round_off_id = False
        CashRounding = self.env["account.cash.rounding"]
        first = CashRounding.search(CashRounding._check_company_domain(self.company), limit=1)
        invoice = self._invoice(650.01)
        invoice.action_round_off()
        self.assertEqual(invoice.invoice_cash_rounding_id, first)

    def test_only_drafts(self):
        invoice = self._invoice(650.01)
        invoice.action_post()
        self.assertFalse(invoice.round_off_available)
        with self.assertRaises(UserError):
            invoice.action_round_off()

    def test_no_method_is_a_clear_refusal(self):
        # the company's own method archived counts as none, like no method at all
        self.env["account.cash.rounding"].search([]).active = False
        invoice = self._invoice(650.01)
        with self.assertRaisesRegex(UserError, "no rounding method"):
            invoice.action_round_off()

    def test_automatic_for_new_customer_invoices(self):
        self.company.invoice_round_off_auto = True
        invoice = self._invoice(650.01)
        self.assertEqual(invoice.invoice_cash_rounding_id, self.nearest_unit)
        self.assertEqual(invoice.amount_total, 650.0)
        # bills are left alone, like Odoo's own automatic rounding
        bill = self._invoice(120.49, move_type="in_invoice", partner=self.partner_b)
        self.assertFalse(bill.invoice_cash_rounding_id)
        self.assertEqual(bill.amount_total, 120.49)

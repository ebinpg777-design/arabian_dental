# Part of ebshel_payment_allocation. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestPaymentAllocation(AccountTestInvoicingCommon):
    """partner_a owes three invoices (600 due 10 Jan, 700 due 10 Feb, 500 due 10 Mar);
    we owe partner_b two bills (300, 450)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank = cls.company_data["default_journal_bank"]
        # partner_b comes with a 30 % / 70 % term: due in full for the bills below, and
        # kept for the one customer invoice that shows instalments as separate items
        cls.partner_b.property_supplier_payment_term_id = cls.pay_terms_a
        cls.inv_a = cls._invoice("out_invoice", cls.partner_a, 600.0, "2026-01-10")
        cls.inv_b = cls._invoice("out_invoice", cls.partner_a, 700.0, "2026-02-10")
        cls.inv_c = cls._invoice("out_invoice", cls.partner_a, 500.0, "2026-03-10")
        cls.bill_a = cls._invoice("in_invoice", cls.partner_b, 300.0, "2026-01-15")
        cls.bill_b = cls._invoice("in_invoice", cls.partner_b, 450.0, "2026-02-15")

    @classmethod
    def _invoice(cls, move_type, partner, amount, date, **kwargs):
        return cls.init_invoice(
            move_type, partner=partner, invoice_date=date, post=True, amounts=[amount],
            taxes=cls.env["account.tax"], **kwargs,
        )

    def _form(self, partner, payment_type="inbound", partner_type="customer"):
        form = Form(
            self.env["account.payment"].with_context(
                default_journal_id=self.bank.id, default_partner_type=partner_type
            )
        )
        form.payment_type = payment_type
        form.partner_id = partner
        return form

    @staticmethod
    def _tick(form, *indexes, allocate=None):
        for index in indexes:
            with form.allocation_ids.edit(index) as line:
                line.selected = True
                if allocate is not None:
                    line.allocate = allocate

    def _rows(self, payment):
        return [(line.move_id, line.selected, line.allocate) for line in payment.allocation_ids]

    def _counterparts(self, payment):
        return payment._seek_for_lines()[1]

    # -- listing ----------------------------------------------------------------------

    def test_lists_the_partners_open_items_oldest_first(self):
        payment = self._form(self.partner_a).save()
        self.assertEqual(
            self._rows(payment),
            [(self.inv_a, False, 0.0), (self.inv_b, False, 0.0), (self.inv_c, False, 0.0)],
        )
        self.assertEqual(payment.allocation_ids.mapped("amount_open"), [600.0, 700.0, 500.0])
        self.assertEqual(payment.amount, 0.0)
        self.assertTrue(payment.amount_from_allocation)
        self.assertIn("Tick what this payment is for", payment.allocation_note)

    def test_items_in_another_currency_are_not_listed(self):
        self._invoice("out_invoice", self.partner_a, 100.0, "2026-01-05",
                      currency=self.setup_other_currency("EUR"))
        payment = self._form(self.partner_a).save()
        self.assertEqual(len(payment.allocation_ids), 3)

    def test_an_invoice_with_instalments_is_one_item_per_due_date(self):
        invoice = self._invoice("out_invoice", self.partner_b, 1000.0, "2026-01-20")
        self.assertEqual(len(invoice.line_ids.filtered(lambda l: l.account_type == "asset_receivable")), 2)
        payment = self._form(self.partner_b).save()
        self.assertEqual(payment.allocation_ids.mapped("move_id"), invoice | invoice)
        self.assertEqual(payment.allocation_ids.mapped("amount_open"), [300.0, 700.0])
        payment.action_allocate_all()
        payment.action_post()
        self.assertEqual(invoice.payment_state, "paid")

    def test_changing_the_partner_relists(self):
        form = self._form(self.partner_a)
        self._tick(form, 0)
        self.assertEqual(form.amount, 600.0)
        form.partner_id = self.partner_b  # a customer with nothing open
        payment = form.save()
        self.assertFalse(payment.allocation_ids)
        self.assertEqual(payment.amount, 0.0)
        self.assertIn("No open invoice or bill", payment.allocation_note)

    # -- the amount and the allocations -------------------------------------------------

    def test_ticked_items_add_up_and_settle(self):
        form = self._form(self.partner_a)
        self._tick(form, 0, 1)
        self.assertEqual(form.amount, 1300.0)
        payment = form.save()
        payment.action_post()

        self.assertEqual(self.inv_a.payment_state, "paid")
        self.assertEqual(self.inv_b.payment_state, "paid")
        self.assertEqual(self.inv_c.payment_state, "not_paid")
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(payment.reconciled_invoice_ids, self.inv_a | self.inv_b)
        counterparts = self._counterparts(payment)
        self.assertEqual(sorted(counterparts.mapped("amount_currency")), [-700.0, -600.0])
        self.assertTrue(all(line.reconciled for line in counterparts))
        self.assertEqual(counterparts.allocation_id, payment.allocation_ids)
        self.assertIn(self.inv_a.name, counterparts.filtered(lambda l: l.amount_currency == -600.0).name)
        # the line that was not ticked is gone: the payment keeps what it settled
        self.assertEqual(self._rows(payment), [(self.inv_a, True, 600.0), (self.inv_b, True, 700.0)])

    def test_lowering_the_amount_trims_the_newest(self):
        form = self._form(self.partner_a)
        self._tick(form, 0, 1)
        form.amount = 1000.0
        payment = form.save()
        self.assertEqual(self._rows(payment)[:2], [(self.inv_a, True, 600.0), (self.inv_b, True, 400.0)])
        self.assertFalse(payment.amount_from_allocation)
        payment.action_post()
        self.assertEqual(self.inv_a.payment_state, "paid")
        self.assertEqual(self.inv_b.payment_state, "partial")
        self.assertEqual(self.inv_b.amount_residual, 300.0)
        self.assertTrue(payment.is_reconciled)

    def test_an_amount_typed_first_fills_what_is_left(self):
        form = self._form(self.partner_a)
        form.amount = 1000.0
        self._tick(form, 0, 1)
        self.assertEqual(form.amount, 1000.0)
        payment = form.save()
        self.assertEqual(self._rows(payment)[:2], [(self.inv_a, True, 600.0), (self.inv_b, True, 400.0)])
        # nothing is left for a third item: it is listed in full and the form objects
        form = Form(payment)
        self._tick(form, 2)
        payment = form.save()
        self.assertEqual(payment.allocated_amount, 1500.0)
        self.assertIn("exceed the payment amount", payment.allocation_warning)
        with self.assertRaises(UserError):
            payment.action_post()

    def test_an_overpayment_stays_unallocated(self):
        form = self._form(self.partner_a)
        self._tick(form, 0)
        form.amount = 1000.0
        payment = form.save()
        self.assertEqual(payment.unallocated_amount, 400.0)
        self.assertIn("not allocated", payment.allocation_note)
        payment.action_post()
        self.assertEqual(self.inv_a.payment_state, "paid")
        self.assertFalse(payment.is_reconciled)
        counterparts = self._counterparts(payment)
        self.assertEqual(sorted(counterparts.mapped("amount_currency")), [-600.0, -400.0])
        self.assertEqual(sum(counterparts.mapped("amount_residual")), -400.0)

    def test_clearing_the_amount_hands_it_back(self):
        form = self._form(self.partner_a)
        self._tick(form, 0, 1)
        form.amount = 1000.0
        form.amount = 0.0
        self.assertEqual(form.amount, 1000.0)  # the trimmed allocations: 600 + 400
        payment = form.save()
        self.assertTrue(payment.amount_from_allocation)

    def test_select_all_then_clear(self):
        payment = self._form(self.partner_a).save()
        payment.action_allocate_all()
        self.assertEqual(payment.allocation_ids.mapped("allocate"), [600.0, 700.0, 500.0])
        self.assertEqual(payment.amount, 1800.0)
        payment.action_allocate_none()
        self.assertEqual(payment.amount, 0.0)
        self.assertFalse(any(payment.allocation_ids.mapped("selected")))

    # -- the other side ---------------------------------------------------------------

    def test_vendor_bills(self):
        form = self._form(self.partner_b, payment_type="outbound", partner_type="supplier")
        self._tick(form, 0, 1)
        self.assertEqual(form.amount, 750.0)
        payment = form.save()
        self.assertEqual([row[0] for row in self._rows(payment)], [self.bill_a, self.bill_b])
        payment.action_post()
        self.assertEqual(self.bill_a.payment_state, "paid")
        self.assertEqual(self.bill_b.payment_state, "paid")
        self.assertEqual(sorted(self._counterparts(payment).mapped("amount_currency")), [300.0, 450.0])
        self.assertEqual(payment.reconciled_bill_ids, self.bill_a | self.bill_b)

    # -- after confirmation -------------------------------------------------------------

    def test_reset_to_draft_and_confirm_again(self):
        form = self._form(self.partner_a)
        self._tick(form, 0, 1)
        payment = form.save()
        payment.action_post()
        payment.action_draft()
        self.assertEqual(self.inv_a.payment_state, "not_paid")
        self.assertEqual(len(self._counterparts(payment)), 1)
        self.assertEqual(self._counterparts(payment).amount_currency, -1300.0)
        self.assertFalse(self._counterparts(payment).allocation_id)
        # only the first invoice this time
        payment.allocation_ids.filtered(lambda l: l.move_id == self.inv_b).write(
            {"selected": False, "allocate": 0.0}
        )
        payment.amount = 600.0
        payment.action_post()
        self.assertEqual(self.inv_a.payment_state, "paid")
        self.assertEqual(self.inv_b.payment_state, "not_paid")
        self.assertEqual(self._rows(payment), [(self.inv_a, True, 600.0)])
        self.assertTrue(payment.is_reconciled)

    def test_too_much_on_one_item_is_refused(self):
        form = self._form(self.partner_a)
        self._tick(form, 0)
        payment = form.save()
        payment.allocation_ids.allocate = 700.0
        payment.amount = 700.0
        self.assertIn("only", payment.allocation_warning)
        with self.assertRaises(UserError):
            payment.action_post()

    def test_an_item_paid_meanwhile_is_refused(self):
        form = self._form(self.partner_a)
        self._tick(form, 0)
        payment = form.save()
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=self.inv_a.ids
        ).create({}).action_create_payments()
        self.assertIn(self.inv_a.payment_state, ("paid", "in_payment"))
        with self.assertRaisesRegex(UserError, "no longer open"):
            payment.action_post()

    def test_wizard_payments_are_untouched(self):
        payments = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=self.inv_c.ids
        ).create({})._create_payments()
        self.assertFalse(payments.allocation_ids)
        self.assertIn(self.inv_c.payment_state, ("paid", "in_payment"))
        self.assertEqual(len(self._counterparts(payments)), 1)

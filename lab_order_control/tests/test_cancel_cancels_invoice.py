# -*- coding: utf-8 -*-
"""A cancelled order stops billing the clinic.

Core cancels only DRAFT invoices with an order, and every lab invoice is posted the
moment it is made, so a cancelled order went on billing: 12 cancelled orders on the
book still carried a posted, paid invoice (client, 2026-10-08: "when cancelling SO
then its related invoice is not cancelled"). The rule chosen: the order's posted
invoices are cancelled with it; an invoice that also bills an order which stays open
is refused, for accounts to credit by hand.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCancelCancelsInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Invoice Cancel Clinic', 'is_company': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Invoice Cancel Appliance', 'type': 'consu', 'list_price': 1000.0,
            'invoice_policy': 'order', 'taxes_id': [(5, 0, 0)]})
        # its own till: a fresh database need not have a cash journal at all
        cls.cash = cls.env['account.journal'].create({
            'name': 'Invoice Cancel Till', 'code': 'ICT', 'type': 'cash',
            'company_id': cls.env.company.id})

    def _order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        order.action_confirm()
        return order

    def _invoice(self, orders):
        invoice = orders._create_invoices()
        invoice.action_post()
        return invoice

    def _receipt(self, invoice, amount):
        """Money received as the lab books it: a cash entry against the receivable."""
        receivable = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.cash.id, 'line_ids': [
                (0, 0, {'account_id': self.cash.default_account_id.id, 'debit': amount}),
                (0, 0, {'account_id': receivable.account_id.id, 'partner_id': self.clinic.id,
                        'credit': amount}),
            ]})
        entry.action_post()
        (receivable | entry.line_ids.filtered(
            lambda l: l.account_id == receivable.account_id)).reconcile()
        return entry

    def test_the_invoice_is_cancelled_with_the_order(self):
        order = self._order()
        invoice = self._invoice(order)
        order.action_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(invoice.state, 'cancel')
        self.assertIn(invoice.name, ' '.join(order.message_ids.mapped('body')),
                      "the order says which invoice went with it")

    def test_a_paid_invoice_gives_its_receipt_back_as_credit(self):
        order = self._order()
        invoice = self._invoice(order)
        receipt = self._receipt(invoice, invoice.amount_total)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment', 'reconciled'), "fixture: paid")
        order.action_cancel()
        self.assertEqual(invoice.state, 'cancel')
        self.assertEqual(receipt.state, 'posted', "the money received is not undone")
        credit = receipt.line_ids.filtered(lambda l: l.partner_id == self.clinic)
        self.assertFalse(credit.reconciled)
        self.assertAlmostEqual(credit.amount_residual, -invoice.amount_total, 2,
                               "it is the clinic's credit now")
        self.assertIn('credit', ' '.join(order.message_ids.mapped('body')))

    def test_an_invoice_shared_with_an_open_order_is_refused(self):
        order, other = self._order(), self._order()
        invoice = self._invoice(order | other)
        with self.assertRaises(UserError) as caught:
            order.action_cancel()
        self.assertIn(other.name, str(caught.exception))
        self.assertEqual(order.state, 'sale', "nothing was half done")
        self.assertEqual(invoice.state, 'posted')

    def test_orders_cancelled_together_take_their_shared_invoice(self):
        order, other = self._order(), self._order()
        invoice = self._invoice(order | other)
        (order | other).action_cancel()
        self.assertEqual(invoice.state, 'cancel')
        self.assertEqual(set((order | other).mapped('state')), {'cancel'})

    def test_a_credit_note_goes_with_its_invoice(self):
        order = self._order()
        invoice = self._invoice(order)
        refund = invoice._reverse_moves()
        refund.action_post()
        order.action_cancel()
        self.assertEqual((invoice | refund).mapped('state'), ['cancel', 'cancel'],
                         "a credit note left posted would owe the clinic for a cancelled order")

    def test_a_draft_invoice_is_still_cancelled_as_core_does(self):
        order = self._order()
        draft = order._create_invoices()
        order.action_cancel()
        self.assertEqual(draft.state, 'cancel')

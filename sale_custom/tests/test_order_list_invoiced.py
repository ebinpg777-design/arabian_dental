# -*- coding: utf-8 -*-
"""The Invoiced column on the order lists.

The lists already colour a fully invoiced row green and carry the status word,
but "partially invoiced" had no number behind it — and on this lab's orders a
part-billed case is the ordinary one. (client, 2026-09-02)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestInvoicedColumn(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Invoiced Col Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Invoiced Col Appliance', 'type': 'consu',
            'invoice_policy': 'order', 'list_price': 100.0})

    def _order(self, qty=2):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': qty,
                                   'price_unit': 100.0,
                                   'tax_ids': [(6, 0, [])]})]})
        order.action_confirm()
        return order

    # --------------------------------------------------------------- the view
    def _list_fields(self, xmlid):
        arch = self.env['sale.order'].get_view(
            self.env.ref(xmlid).id, 'list')['arch']
        from lxml import etree
        return [node.get('name')
                for node in etree.fromstring(arch).iter('field')]

    def test_the_column_is_on_the_order_list(self):
        self.assertIn('amount_invoiced',
                      self._list_fields('sale.view_order_tree'))

    def test_the_column_is_on_the_quotation_list(self):
        self.assertIn('amount_invoiced',
                      self._list_fields('sale.view_quotation_tree'))

    def test_the_column_never_asks_a_grouped_list_to_sum_it(self):
        """amount_invoiced is not stored, so a `sum` on it would send a
        grouped list to read_group for a column SQL cannot aggregate."""
        from lxml import etree
        for xmlid in ('sale.view_order_tree', 'sale.view_quotation_tree'):
            arch = self.env['sale.order'].get_view(
                self.env.ref(xmlid).id, 'list')['arch']
            node = etree.fromstring(arch).xpath(
                "//field[@name='amount_invoiced']")[0]
            self.assertIsNone(node.get('sum'))
            self.assertIsNone(node.get('avg'))

    def test_a_grouped_read_of_the_list_still_works(self):
        """The whole point of the check above, exercised rather than asserted."""
        # `formatted_read_group`, not `read_group`: the latter is deprecated
        # since 19.0, and this test exists to exercise the client-facing
        # grouped read rather than the backend one. (2026-09-12)
        self.env['sale.order'].formatted_read_group(
            [('partner_id', '=', self.clinic.id)],
            ['partner_id'], ['amount_total:sum'])

    # -------------------------------------------------------------- the value
    def test_nothing_invoiced_reads_as_zero(self):
        self.assertEqual(self._order().amount_invoiced, 0.0)

    def test_a_part_billed_order_shows_what_was_billed(self):
        order = self._order(qty=2)
        order.order_line.qty_to_invoice = 1
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertEqual(order.amount_invoiced, 100.0,
                         "half of a 200 order, billed, reads as 100")
        self.assertLess(order.amount_invoiced, order.amount_total,
                        "and it is visibly short of the order total")

    def test_a_fully_billed_order_matches_its_total(self):
        order = self._order(qty=2)
        order._create_invoices().action_post()
        self.assertEqual(order.amount_invoiced, order.amount_total)

    def test_a_draft_invoice_is_not_money_billed_yet(self):
        order = self._order()
        order._create_invoices()          # left in draft on purpose
        self.assertEqual(order.amount_invoiced, 0.0,
                         "a draft invoice has not billed the doctor anything")

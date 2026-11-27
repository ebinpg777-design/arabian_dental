# -*- coding: utf-8 -*-
"""PO and the Purchase Request's RFQ to a vendor on WhatsApp (client, 2026-11-27)."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseWhatsapp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env['epg.whatsapp.account']
        Account.search([]).write({'active': False})   # the sender made here is the default
        cls.account = Account.create({'name': 'PO Sender', 'channel': 'link',
                                      'company_id': cls.env.company.id})
        number_field = 'mobile' if 'mobile' in cls.env['res.partner']._fields else 'phone'
        cls.vendor = cls.env['res.partner'].create({'name': 'WA Vendor Supplies', number_field: '9847012345'})
        cls.uom = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'WA Plaster 25 kg', 'purchase_ok': True, 'uom_id': cls.uom.id})

    def _payload(self, action):
        self.assertEqual(action['tag'], 'epg_whatsapp_send')
        return self.env['epg.whatsapp.message'].browse(action['params']['message_ids'])

    def test_the_po_goes_as_its_pdf(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 2, 'price_unit': 900})],
        })
        messages = self._payload(po.action_whatsapp_vendor())
        self.assertEqual(len(messages), 1)
        self.assertIn('Request for Quotation', messages.body, "not yet confirmed: the RFQ")
        self.assertTrue(messages.attachment_id.name.endswith('.pdf'))
        self.assertTrue(messages.number.endswith('9847012345'))
        po.button_confirm()
        messages = self._payload(po.action_whatsapp_vendor())
        self.assertIn('Purchase Order', messages.body)
        self.assertTrue(bytes(messages.attachment_id.raw))

    def test_the_rfq_goes_as_pdf_and_excel_to_the_vendor(self):
        pr = self.env['ebshel.purchase.request'].create({
            'description': 'WA PR', 'requester_id': self.env.uid,
            'line_ids': [(0, 0, {'product_id': self.product.id, 'name': 'WA Plaster 25 kg',
                                 'qty': 5.0, 'uom_id': self.uom.id, 'price_unit': 100.0})],
        })
        rfq = self.env['ebshel.purchase.rfq'].create({
            'rfq_line_ids': [(0, 0, {'request_line_id': pr.line_ids.id, 'product_id': self.product.id,
                                     'name': 'WA Plaster 25 kg', 'qty': 5.0, 'uom_id': self.uom.id})],
        })
        with self.assertRaises(UserError):
            rfq.action_whatsapp_vendors()          # no vendor yet
        self.env['ebshel.purchase.vendor.quotation'].create({'rfq_id': rfq.id, 'vendor_id': self.vendor.id})
        messages = self._payload(rfq.action_whatsapp_vendors())
        self.assertEqual(len(messages), 2, "the PDF and the Excel sheet, as one WhatsApp")
        self.assertEqual(set(messages.mapped('number')), {messages[0].number})
        names = messages.mapped('attachment_id.name')
        self.assertTrue(names[0].endswith('.pdf') and names[1].endswith('.xlsx'), names)
        self.assertTrue(bytes(messages[1].attachment_id.raw).startswith(b'PK'))
        self.assertIn('yellow cells', messages[0].body)

    def test_a_vendor_without_a_number_is_told(self):
        nobody = self.env['res.partner'].create({'name': 'WA No Number'})
        po = self.env['purchase.order'].create({'partner_id': nobody.id})
        with self.assertRaises(UserError):
            po.action_whatsapp_vendor()

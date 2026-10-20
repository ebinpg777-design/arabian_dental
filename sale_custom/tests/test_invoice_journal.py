# -*- coding: utf-8 -*-
"""B2B or B2C by the clinic's GST treatment, as v17 dental_sale did.

Without it Odoo 20 numbered new invoices in the first sales journal, which on the live
book was Opening Invoices: OB/2026/00001 (client, 2026-10-20: "Invoice sequences are not
correct").
"""
from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestInvoiceJournal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Journal = cls.env['account.journal']
        cls.b2b = Journal._lab_gst_journal('regular', cls.env.company)
        cls.b2c = Journal._lab_gst_journal('unregistered', cls.env.company)
        if not cls.b2b:
            cls.b2b = Journal.create({'name': 'B2B', 'code': 'B2B', 'type': 'sale', 'sequence': 998})
        if not cls.b2c:
            cls.b2c = Journal.create({'name': 'B2C', 'code': 'B2C', 'type': 'sale', 'sequence': 999})
        cls.default = cls.env['account.move'].with_context(default_move_type='out_invoice').new(
            {'move_type': 'out_invoice'}).journal_id
        india = cls.env.ref('base.in')
        gujarat = cls.env['res.country.state'].search([('country_id', '=', india.id), ('code', '=', 'GJ')], limit=1)
        cls.route = cls.env['crm.team'].create({'name': 'IJ Route'})
        cls.registered = cls.env['res.partner'].create({
            'name': 'IJ Registered Clinic', 'team_id': cls.route.id, 'country_id': india.id,
            'state_id': gujarat.id, 'vat': '24ABCPM8965E1ZE', 'l10n_in_gst_treatment': 'regular'})
        cls.billing = cls.env['res.partner'].create({
            'name': 'IJ Registered Clinic Accounts', 'type': 'invoice', 'parent_id': cls.registered.id})
        cls.plain = cls.env['res.partner'].create({
            'name': 'IJ Plain Clinic', 'team_id': cls.route.id, 'l10n_in_gst_treatment': 'unregistered'})
        cls.patient = cls.env['res.partner'].create({
            'name': 'IJ Walk-in', 'team_id': cls.route.id, 'l10n_in_gst_treatment': 'consumer'})
        cls.abroad = cls.env['res.partner'].create({
            'name': 'IJ Overseas Clinic', 'team_id': cls.route.id, 'l10n_in_gst_treatment': 'overseas'})
        cls.product = cls.env['product.product'].create({
            'name': 'IJ Crown', 'type': 'service', 'invoice_policy': 'order', 'list_price': 100.0})

    def _order(self, partner, **vals):
        order = self.env['sale.order'].create(dict({
            'partner_id': partner.id, 'patient': 'Ravi Menon',
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1,
                                   'price_unit': 100.0, 'tax_ids': [(6, 0, [])]})]}, **vals))
        order.action_confirm()
        return order

    def test_the_two_series_exist_and_differ(self):
        self.assertTrue(self.b2b and self.b2c)
        self.assertNotEqual(self.b2b, self.b2c)

    def test_a_registered_clinic_is_invoiced_b2b(self):
        invoice = self._order(self.registered)._create_invoices()
        self.assertEqual(invoice.journal_id, self.b2b)
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')

    def test_an_unregistered_clinic_is_invoiced_b2c(self):
        invoice = self._order(self.plain)._create_invoices()
        self.assertEqual(invoice.journal_id, self.b2c)
        invoice.action_post()
        self.assertTrue(invoice.name.startswith(self.b2c.code), invoice.name)

    def test_a_consumer_is_invoiced_b2c(self):
        self.assertEqual(self._order(self.patient)._create_invoices().journal_id, self.b2c)

    def test_a_clinic_with_no_treatment_and_no_gstin_is_a_consumer(self):
        # l10n_in's own fallback on the invoice; most of the lab's test clinics look like this
        bare = self.env['res.partner'].create({'name': 'IJ Bare Clinic', 'team_id': self.route.id})
        invoice = self._order(bare)._create_invoices()
        self.assertEqual(invoice.l10n_in_gst_treatment, 'consumer')
        self.assertEqual(invoice.journal_id, self.b2c)
        invoice.action_post()

    def test_a_clinic_with_no_treatment_but_a_gstin_is_registered(self):
        gujarat = self.registered.state_id
        bare = self.env['res.partner'].create({
            'name': 'IJ GSTIN Clinic', 'team_id': self.route.id, 'country_id': gujarat.country_id.id,
            'state_id': gujarat.id, 'vat': '24ABCPM8965E1ZE'})
        bare.l10n_in_gst_treatment = False
        invoice = self._order(bare)._create_invoices()
        self.assertEqual(invoice.l10n_in_gst_treatment, 'regular')
        self.assertEqual(invoice.journal_id, self.b2b)

    def test_the_billing_address_follows_its_clinic(self):
        invoice = self._order(self.registered, partner_invoice_id=self.billing.id)._create_invoices()
        self.assertEqual(invoice.journal_id, self.b2b)

    def test_other_treatments_keep_the_default_journal(self):
        self.assertEqual(self._order(self.abroad)._create_invoices().journal_id, self.default)

    def test_a_journal_chosen_on_the_order_is_kept(self):
        other = self.env['account.journal'].create({'name': 'IJ Other', 'code': 'IJOT', 'type': 'sale', 'sequence': 1000})
        invoice = self._order(self.abroad, journal_id=other.id)._create_invoices()
        self.assertEqual(invoice.journal_id, other)

    def test_a_hand_typed_invoice_follows_the_clinic(self):
        form = Form(self.env['account.move'].with_context(default_move_type='out_invoice'))
        form.partner_id = self.registered
        self.assertEqual(form.journal_id, self.b2b)
        form.partner_id = self.plain
        self.assertEqual(form.journal_id, self.b2c)

    def test_an_invoice_in_the_wrong_series_is_refused(self):
        invoice = self._order(self.plain)._create_invoices()
        invoice.journal_id = self.b2b
        with self.assertRaisesRegex(ValidationError, 'Journal mismatch'):
            invoice.action_post()

    def test_the_migration_replay_is_not_checked(self):
        invoice = self._order(self.plain)._create_invoices()
        invoice.journal_id = self.b2b
        invoice.with_context(migration_replay=True).action_post()
        self.assertEqual(invoice.state, 'posted')

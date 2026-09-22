# -*- coding: utf-8 -*-
"""Eight work items to a sheet, and never a blank one.

The sheet is built from manufacturing orders, but roughly one confirmed order in five
in this database has none - and a report that prints nothing for those is, at the
counter, indistinguishable from a broken printer. So an order with no MO still gets a
slip per line, carrying the sales order's own number as its barcode. (client,
2026-08-28)
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.sale_custom.report.production_slip import PER_PAGE, _pages, slips_for_orders


@tagged('post_install', '-at_install')
class TestProductionSlips(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Slip Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Slip Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, lines=1):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Meera',
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})
                           for _ in range(lines)]})

    def test_an_order_without_manufacturing_still_prints(self):
        order = self._order(lines=2)
        slips = slips_for_orders(order)
        self.assertEqual(len(slips), 2, "one slip per line when there is no MO behind it")
        for slip in slips:
            self.assertEqual(slip['mo'], '', "there is no manufacturing order to name")
            self.assertEqual(slip['so'], order.name)
            self.assertEqual(slip['code'], order.name,
                             "the barcode falls back to the number the scanner reads")
            self.assertEqual(slip['partner'], self.clinic.display_name)
            self.assertEqual(slip['patient'], 'Meera')

    def test_manufacturing_orders_win_over_lines(self):
        order = self._order(lines=2)
        # sale_id is related to sale_line_id.order_id, so the LINE is what ties a job
        # to its order - writing sale_id directly would be silently undone.
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1,
            'sale_line_id': order.order_line[0].id})
        slips = slips_for_orders(order)
        self.assertEqual([s['mo'] for s in slips], [mo.name],
                         "one slip for the MO, not two for the lines it came from")
        self.assertEqual(slips[0]['code'], mo.name, "the MO's own number is the barcode")
        self.assertEqual(slips[0]['so'], order.name, "and the order is still named on it")

    def test_a_cancelled_job_is_not_printed(self):
        order = self._order()
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1,
            'sale_line_id': order.order_line[0].id})
        mo.action_cancel()
        slips = slips_for_orders(order)
        self.assertTrue(slips, "the order still prints - the work has not gone away")
        self.assertEqual(slips[0]['mo'], '', "but not under a cancelled MO's number")

    # ------------------------------------------------------------- urgency (2026-08-29)
    def test_urgent_and_emergency_orders_carry_it_onto_the_slip(self):
        urgent = self._order()
        urgent.priority = 'urgent'
        emergency = self._order()
        emergency.priority = 'emergency'
        normal = self._order()

        self.assertEqual(slips_for_orders(urgent)[0]['priority'], 'urgent')
        self.assertEqual(slips_for_orders(emergency)[0]['priority'], 'emergency')
        self.assertEqual(slips_for_orders(normal)[0]['priority'], '',
                         "an ordinary case carries no banner")

    def test_urgency_survives_onto_a_slip_raised_from_a_manufacturing_order(self):
        """The MO path builds its slip differently from the line path - both have
        to read priority off the order behind them, not just one of the two."""
        order = self._order()
        order.priority = 'emergency'
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1,
            'sale_line_id': order.order_line[0].id})
        slips = slips_for_orders(order)
        self.assertEqual(slips[0]['mo'], mo.name)
        self.assertEqual(slips[0]['priority'], 'emergency')

    def test_a_low_or_missing_priority_never_reads_as_urgent(self):
        low = self._order()
        low.priority = 'low'
        self.assertEqual(slips_for_orders(low)[0]['priority'], '')

    def test_pages_are_chunked_by_eight(self):
        self.assertEqual(PER_PAGE, 8)
        self.assertEqual([len(p) for p in _pages(range(20))], [8, 8, 4])
        self.assertEqual(_pages([]), [], "no work, no pages")

    def test_the_report_renders_a_pdf(self):
        order = self._order(lines=2)
        report = self.env.ref('sale_custom.action_report_production_slips_sale')
        # Reports render as HTML under the test runner unless asked for the real
        # thing, and the whole point here is that wkhtmltopdf survives the layout.
        pdf, kind = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            report.report_name, order.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_the_report_still_renders_with_an_emergency_banner_on_it(self):
        """The corner badge is absolutely positioned - the one layout choice here
        wkhtmltopdf could plausibly choke on or place somewhere wrong."""
        order = self._order(lines=2)
        order.priority = 'emergency'
        report = self.env.ref('sale_custom.action_report_production_slips_sale')
        pdf, kind = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            report.report_name, order.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))


@tagged('post_install', '-at_install')
class TestReworkOnTheSlip(TransactionCase):
    """A remake says which case it is remaking.

    The rework fields belong to lab_rework, which depends on this module and not
    the other way round, so the slip reads them defensively and this class skips
    itself where that module is not installed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Rework Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Rework Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id, 'patient': 'Anu',
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        }, **vals))

    def _slip(self, order):
        return slips_for_orders(order)[0]

    def test_an_ordinary_order_says_nothing_about_reworks(self):
        slip = self._slip(self._order())
        self.assertFalse(slip['rework'])
        self.assertEqual((slip['rework_of'], slip['rework_date']), ('', ''))

    def test_a_rework_carries_the_original_number_and_its_date(self):
        if 'rework_origin_id' not in self.env['sale.order']._fields:
            self.skipTest("lab_rework is not installed")
        original = self._order()
        original.date_order = '2026-06-12 09:30:00'
        rework = self._order(is_rework=True, rework_origin_id=original.id)
        slip = self._slip(rework)
        self.assertTrue(slip['rework'])
        self.assertEqual(slip['rework_of'], original.name)
        self.assertTrue(slip['rework_date'], "the day the original was placed")
        # The day HERE, not the day in UTC: 09:30 UTC is the afternoon in this lab,
        # and a slip that prints the 11th for a job placed on the 12th is a slip
        # the office cannot match against its own paperwork.
        self.assertIn('12', slip['rework_date'])

    def test_a_rework_with_no_original_recorded_says_so(self):
        if 'is_rework' not in self.env['sale.order']._fields:
            self.skipTest("the rework flag is not installed")
        slip = self._slip(self._order(is_rework=True))
        self.assertTrue(slip['rework'], "the bench is still told it is a remake")
        self.assertEqual(slip['rework_of'], '',
                         "2,686 migrated reworks have no original on file")

    def test_the_slip_prints_the_original_on_the_page(self):
        if 'rework_origin_id' not in self.env['sale.order']._fields:
            self.skipTest("lab_rework is not installed")
        original = self._order()
        rework = self._order(is_rework=True, rework_origin_id=original.id)
        html = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.report_production_slips_sale', rework.ids)[0].decode()
        self.assertIn('Rework of', html)
        self.assertIn(original.name, html)

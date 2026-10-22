# -*- coding: utf-8 -*-
"""The case sticker is the code to scan (client, 2026-10-22: "change the scan in Field
executive to the sticker barcode of each SO"). One sticker per order, its barcode the
order number, printed down the label's right edge - so a sticker held upright lays the
bars flat, and the readers must take them that way round."""
import base64
import io
import re

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestScanTheSticker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Sticker Clinic', 'is_clinic': True,
            'partner_latitude': 9.9312, 'partner_longitude': 76.2673})
        cls.product = cls.env['product.product'].create({
            'name': 'Sticker Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.Delivery = cls.env['lab.delivery']

    def _order(self, patient='Rani'):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': patient,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        return order

    def test_each_orders_sticker_is_one_box_where_the_invoice_asks(self):
        first, second = self._order('Rani'), self._order('Latha')
        invoice = (first + second)._create_invoices()
        invoice.action_post()
        self.assertEqual(self.Delivery.scan(invoice.name)['context']['lab_scan_summary']['state'],
                         'several', "an invoice for two cases has to ask which box is in the hand")
        for order in (first, second):
            found = self.Delivery.scan(order.name)['context']['lab_scan_summary']
            self.assertEqual(found['order'], order.name, "the sticker names its own case")
            self.assertEqual(self.Delivery.search_count([('sale_order_id', '=', order.id)]), 1)

    def test_a_parcel_sticker_lists_its_orders_and_is_answered_with_them(self):
        """A sticker made for a clinic's or a route's whole parcel carries every order
        number, "S1, S2" - each one a case of its own."""
        first, second = self._order(), self._order()
        code = '%s , %s' % (first.name.lower(), second.name)
        action = self.Delivery.scan(code)
        self.assertEqual(action['res_model'], 'sale.order')
        self.assertEqual(sorted(action['domain'][0][2]), sorted((first + second).ids))
        self.assertEqual(action['context']['lab_scan_summary']['count'], 2)
        # one order on a parcel sticker is simply that case
        found = self.Delivery.scan(first.name + ',')['context']['lab_scan_summary']
        self.assertEqual(found['order'], first.name)
        with self.assertRaises(UserError, msg="commas alone match nothing - never every order"):
            self.Delivery.scan(' , , ')

    def test_the_unknown_code_points_at_the_sticker(self):
        with self.assertRaises(UserError) as caught:
            self.Delivery.scan('NOT-A-REAL-CODE')
        self.assertIn("case's sticker", str(caught.exception))

    def test_the_lab_reads_an_upright_sticker_from_a_photo(self):
        """The server's reader, on a photo of a sticker held upright: the bars of its
        barcode lie flat across the frame."""
        if 'epg.sticker.engine' not in self.env:
            self.skipTest("the sticker printer is not installed here")
        try:
            import zxingcpp  # noqa: F401
            from PIL import Image, ImageDraw, ImageFilter
        except ImportError:
            self.skipTest("zxing-cpp is not installed here")
        order = self._order()
        svg = self.env['epg.sticker.engine'].code128_svg(order.name)
        total = int(re.search(r'viewBox="0 0 (\d+) 100"', svg).group(1))
        module = 4
        bars = Image.new('L', (total * module, 150), 255)
        draw = ImageDraw.Draw(bars)
        for x, width in re.findall(r'<rect x="(\d+)" y="0" width="(\d+)" height="100"/>', svg):
            draw.rectangle([int(x) * module, 0, (int(x) + int(width)) * module - 1, 149], fill=0)
        upright = bars.rotate(90, expand=True)          # the sticker held upright
        photo = Image.new('RGB', (1280, 900), (205, 200, 190))
        label = Image.new('RGB', (upright.width + 520, upright.height + 120), 'white')
        label.paste(upright.convert('RGB'), (label.width - upright.width - 40, 60))
        photo.paste(label.rotate(4, expand=True, fillcolor=(205, 200, 190)), (200, 60))
        photo = photo.filter(ImageFilter.GaussianBlur(0.8))
        buffer = io.BytesIO()
        photo.save(buffer, 'JPEG', quality=85)
        codes = self.Delivery.decode_photo(base64.b64encode(buffer.getvalue()).decode())
        self.assertIn(order.name, codes)

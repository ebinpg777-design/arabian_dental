# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models import ir_actions_report as fallback


@tagged('post_install', '-at_install')
class TestAutoSymbology(TransactionCase):

    def test_auto_is_resolved_like_core(self):
        resolve = self.env['ir.actions.report']._core_symbology
        self.assertEqual(resolve('auto', '5901234123457'), 'EAN13')
        self.assertEqual(resolve('auto', '96385074'), 'EAN8')
        self.assertEqual(resolve('auto', 'LOT-0042'), 'Code128')
        # right length, wrong check digit: core falls back to Code128
        self.assertEqual(resolve('auto', '5901234123458'), 'Code128')
        self.assertEqual(resolve('auto', '12345678'), 'Code128')
        self.assertEqual(resolve('EAN13', '5901234123458'), 'Code128')
        self.assertEqual(resolve('Code39', '5901234123457'), 'Code39')

    def test_auto_draws_an_ean13_for_an_ean13_value(self):
        if fallback.pybarcode is None:
            self.fail("python-barcode is a declared external dependency of this module")
        drawn = []
        real = fallback.pybarcode.get_barcode_class

        def spy(name):
            drawn.append(name)
            return real(name)

        with patch.object(fallback.pybarcode, 'get_barcode_class', side_effect=spy):
            png = self.env['ir.actions.report']._linear_png('auto', '5901234123457', 300, 100, {})
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(drawn[0], 'ean13')

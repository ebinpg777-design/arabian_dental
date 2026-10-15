# -*- coding: utf-8 -*-
"""The work order's form: every name beside its own label."""
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkorderForm(TransactionCase):

    def _arch(self):
        view = self.env.ref('mrp.mrp_production_workorder_form_view_inherit')
        return etree.fromstring(self.env['mrp.workorder'].get_view(view.id, 'form')['arch'])

    def test_the_technicians_sit_beside_their_labels(self):
        """In a form group a field fills two cells (label and value) and anything else
        one: a button without colspan="2" pushed every label after it out of line, the
        Technician's name landing under the button. (client screenshot, 2026-10-14)"""
        group = self._arch().xpath("//field[@name='workcenter_id']/parent::group")[0]
        children = [child for child in group if isinstance(child.tag, str)]     # the compiler skips comments
        names = [child.get('name') for child in children]
        self.assertLess(names.index('workcenter_id'), names.index('bench_user_id'))
        self.assertIn('wc_scan_button', names)
        for child in children:
            if child.tag in ('field', 'newline') or child.get('invisible') in ('1', 'True'):
                continue
            with self.subTest(element=child.tag, name=child.get('name')):
                self.assertEqual(child.get('colspan'), '2', "a non-field in the group must fill a whole row")

    def test_the_legacy_technician_shows_only_when_it_says_something_else(self):
        arch = self._arch()
        legacy = arch.xpath("//field[@name='user_id'][not(ancestor::field)]")
        if not legacy:
            self.skipTest("sale_custom's legacy technician is not on this form")
        self.assertEqual(legacy[0].get('invisible'), "not user_id or user_id == bench_user_id")
        self.assertTrue(arch.xpath("//field[@name='time_ids']//field[@name='user_id']"),
                        "the time-tracking list keeps its User column")

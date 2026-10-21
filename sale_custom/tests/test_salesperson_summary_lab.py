# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalespersonSummaryLab(TransactionCase):
    """The Salesperson Summary prints on the lab letterhead. (client, 2026-10-21)"""

    def test_the_lab_letterhead_and_paper(self):
        wizard = self.env['salesperson.summary.wizard'].create({'period': 'this_month'})
        self.assertEqual(wizard._layout(), 'sale_custom.external_layout_lab')
        report = self.env.ref('ebshel_salesperson_summary.action_report_salesperson_summary')
        self.assertEqual(report.paperformat_id, self.env.ref('sale_custom.paperformat_lab_invoice'))
        html = self.env['ir.actions.report']._render_qweb_html(report.report_name, wizard.ids)[0]
        self.assertIn(b'lab-letterhead', html)

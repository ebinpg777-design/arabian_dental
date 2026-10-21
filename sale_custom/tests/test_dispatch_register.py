# -*- coding: utf-8 -*-
"""The dispatch register (client, 2026-10-21): the v17 sheet - one row per invoice
line with boxes to tick - one Sales Route per page, the selected routes or every
route; and the letterhead/footer flags that never reached the layout."""
from datetime import date

from odoo.tests import TransactionCase, tagged

DAY = date(2031, 3, 3)          # far from any real invoice in the database
NEXT = date(2031, 3, 4)


@tagged('post_install', '-at_install')
class TestDispatchRegister(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Team = cls.env['crm.team']
        cls.route_a = Team.create({'name': 'ZZ TEST ROUTE A'})
        cls.route_b = Team.create({'name': 'ZZ TEST ROUTE B'})
        Partner = cls.env['res.partner']
        cls.dr_a = Partner.create({'name': 'DR. VIPIN SASI, CHERUPULASSERI', 'team_id': cls.route_a.id})
        cls.dr_b = Partner.create({'name': 'SAFIYA DC, PUZHAKATTIRI', 'team_id': cls.route_b.id})
        Product = cls.env['product.product']
        cls.crown = Product.create({'name': 'BASIC Metal Ceramic FULL COVERING', 'default_code': 'CE1026',
                                    'type': 'consu', 'list_price': 100.0})
        cls.hawley = Product.create({'name': 'HAWLEYS PER JAW', 'default_code': 'OT1094',
                                     'type': 'consu', 'list_price': 100.0})

        def invoice(partner, when, lines, move_type='out_invoice'):
            return cls.env['account.move'].create({
                'move_type': move_type, 'partner_id': partner.id, 'invoice_date': when,
                'invoice_line_ids': [(0, 0, {'product_id': product.id, 'quantity': 1,
                                             'price_unit': 100.0, 'patient': patient})
                                     for product, patient in lines],
            })
        # route A: one invoice, two patients (Zara's line first on the invoice)
        cls.inv_a = invoice(cls.dr_a, DAY, [(cls.crown, 'ZARA'), (cls.hawley, 'ANU')])
        # route B: one invoice that day, one the next day, and a credit note
        cls.inv_b = invoice(cls.dr_b, DAY, [(cls.hawley, 'SHIFIN')])
        cls.inv_b_next = invoice(cls.dr_b, NEXT, [(cls.crown, 'NEDHA')])
        cls.refund_b = invoice(cls.dr_b, DAY, [(cls.crown, 'SHIFIN')], move_type='out_refund')
        cls.Wizard = cls.env['dental.dispatch.register']
        cls.Report = cls.env['report.sale_custom.report_dispatch_register']

    def _wizard(self, **vals):
        return self.Wizard.create(dict({'date': DAY, 'include_draft': True}, **vals))

    def _ours(self, groups):
        return [g for g in groups if g['name'] in (self.route_a.name, self.route_b.name)]

    def _html(self, wizard):
        html, _kind = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.report_dispatch_register', wizard.ids)
        return html.decode()

    def test_one_row_per_line_route_by_route(self):
        groups = self._ours(self.Report._groups(self._wizard()))
        self.assertEqual([g['name'] for g in groups], ['ZZ TEST ROUTE A', 'ZZ TEST ROUTE B'])
        a, b = groups
        self.assertEqual((a['invoice_count'], a['item_count']), (1, 2))
        self.assertEqual((b['invoice_count'], b['item_count']), (1, 1),
                         "the next day's invoice and the credit note are not on it")
        inv = a['invoices'][0]
        self.assertEqual((inv['date'], inv['doctor'], inv['draft']),
                         ('03/03/2031', 'DR. VIPIN SASI, CHERUPULASSERI', True))
        self.assertEqual(inv['rows'], [
            {'patient': 'ANU', 'product': '[OT1094] HAWLEYS PER JAW'},
            {'patient': 'ZARA', 'product': '[CE1026] BASIC Metal Ceramic FULL COVERING'},
        ], "a patient's works together, as the v17 sheet had them")

    def test_each_route_is_a_page_with_the_letterhead_and_the_footer_note(self):
        wizard = self._wizard(team_ids=[(6, 0, (self.route_a | self.route_b).ids)])
        html = self._html(wizard)
        self.assertEqual(html.count('odr-route '), 2)
        self.assertEqual(html.count('odr-newpage'), 1, "the second route starts a page")
        self.assertEqual(html.count('class="lab-letterhead"'), 2, "every route page opens with it")
        self.assertNotIn('header lab-header', html,
                         "the compact page header is not drawn over the letterhead")
        self.assertIn('>Dispatch Register</td>', html, "the footer carries its note")
        for text in ('ROUTE · ZZ TEST ROUTE A', 'ROUTE · ZZ TEST ROUTE B', 'Invoiced', 'Packed',
                     'Despatch', 'Remarks', 'HAWLEYS PER JAW', 'SHIFIN', 'Despatched by'):
            self.assertIn(text, html)
        self.assertEqual(html.count('class="odr-box"'), 3 * 3, "three boxes on each of three rows")

    def test_a_selected_route_prints_alone_and_a_range_takes_the_days(self):
        groups = self.Report._groups(self._wizard(team_ids=[(6, 0, self.route_b.ids)]))
        self.assertEqual([g['name'] for g in groups], ['ZZ TEST ROUTE B'])
        self.assertEqual(groups[0]['invoice_count'], 1)
        groups = self.Report._groups(self._wizard(team_ids=[(6, 0, self.route_b.ids)], date_to=NEXT))
        self.assertEqual(groups[0]['invoice_count'], 2)
        self.assertEqual([i['date'] for i in groups[0]['invoices']], ['03/03/2031', '04/03/2031'])
        values = self.Report._get_report_values(self._wizard(date_to=NEXT).ids)
        self.assertEqual(values['date_label'], '03/03/2031 – 04/03/2031')

    def test_drafts_only_on_request_and_the_summary_says_so(self):
        posted_only = self._wizard(include_draft=False, team_ids=[(6, 0, self.route_a.ids)])
        self.assertFalse(posted_only._moves(), "the test invoices are drafts")
        self.assertEqual(posted_only.summary, 'No invoices for this selection.')
        html = self._html(posted_only)
        self.assertIn('No invoices dated 03/03/2031 for ZZ TEST ROUTE A', html)
        self.assertNotIn('header lab-header', html)
        wizard = self._wizard(team_ids=[(6, 0, (self.route_a | self.route_b).ids)])
        self.assertIn('2 invoice(s) · 3 item(s) · 2 route(s)', wizard.summary)

    def test_the_order_reports_keep_their_letterhead_and_footer_too(self):
        """The same flags, set on the wrong t-call, broke the order list and the order
        count as well (2026-10-21)."""
        Report = self.env['ir.actions.report']
        listing = self.env['sale.order.list.report'].create({'date_from': DAY, 'date_to': DAY})
        html = Report._render_qweb_html('sale_custom.report_order_list', listing.ids)[0].decode()
        self.assertNotIn('header lab-header', html)
        self.assertIn('>Order list</td>', html)
        count = self.env['sale.order.count.report'].create({'date_from': DAY, 'date_to': DAY})
        html = Report._render_qweb_html('sale_custom.report_order_count', count.ids)[0].decode()
        self.assertNotIn('header lab-header', html)
        self.assertIn('>Order count summary</td>', html)

# Part of ebshel_invoice_analysis_fix. See LICENSE file for full copyright and licensing details.
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

AMOUNTS = ('price_subtotal', 'price_total', 'price_margin', 'inventory_value')


@tagged('post_install', '-at_install')
class TestInvoiceAnalysis(AccountTestInvoicingCommon):

    def _pivot_cell(self, fname, domain):
        """The value the pivot shows for `fname` over `domain`, read the way the
        Odoo 20 web client reads it (web/static/src/views/pivot/pivot_model.js,
        _getMeasureSpecs and _getMeasurements): the field's own aggregate, the
        record currencies, and a converted sum used only when they are mixed."""
        Report = self.env['account.invoice.report']
        field = Report.fields_get([fname], ['aggregator', 'currency_field'])[fname]
        specs = [
            f"{fname}:{field['aggregator']}",
            f"{field['currency_field']}:array_agg_distinct",
            f"{fname}:sum_currency",
        ]
        [group] = Report.formatted_read_group(domain, [], specs)
        value = None
        for spec in specs:
            name, aggregator = spec.split(':')
            if aggregator == 'array_agg_distinct':
                continue
            if aggregator == 'sum_currency' and len(group[f"{field['currency_field']}:array_agg_distinct"]) == 1:
                continue
            value = group[spec]
        return value

    def test_amounts_are_plain_sums(self):
        fields = self.env['account.invoice.report'].fields_get(list(AMOUNTS), ['aggregator'])
        for fname in AMOUNTS:
            self.assertEqual(fields[fname]['aggregator'], 'sum', fname)

    def test_the_pivot_shows_the_untaxed_amount(self):
        invoice = self.init_invoice('out_invoice', amounts=[1000.0, 250.0], post=True)
        domain = [('move_id', '=', invoice.id)]
        self.assertAlmostEqual(self._pivot_cell('price_subtotal', domain), invoice.amount_untaxed)
        self.assertAlmostEqual(self._pivot_cell('price_total', domain), invoice.amount_total)

    def test_a_refund_counts_against_the_invoice(self):
        invoice = self.init_invoice('out_invoice', amounts=[1000.0], post=True)
        refund = self.init_invoice('out_refund', amounts=[400.0], post=True)
        domain = [('move_id', 'in', (invoice + refund).ids)]
        self.assertAlmostEqual(self._pivot_cell('price_subtotal', domain), 600.0)

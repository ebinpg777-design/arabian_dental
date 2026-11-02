# Part of ebshel_invoice_analysis_fix. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class AccountInvoiceReport(models.Model):
    """The report's amounts summed like every other amount in Odoo.

    Odoo 20 declares them with the `sum_currency` aggregator. The web client asks
    for `<field>:sum_currency` beside each monetary field's own aggregate, as the
    converted figure for groups that mix currencies, and drops it in groups with
    one currency - so for these fields it dropped their own figure as well, and
    the pivot and the grouped list showed nothing. (Odoo fixed the client in
    odoo/odoo@714bc0538121 on 2026-10-08.)

    As a plain `sum` the field takes the client's ordinary path: the sum in a
    group with one currency, the converted sum in a group that mixes them. The
    Average Price (`avg_currency`) was always shown and is left alone.
    """
    _inherit = 'account.invoice.report'

    price_subtotal = fields.Monetary(aggregator='sum')
    price_total = fields.Monetary(aggregator='sum')
    price_margin = fields.Monetary(aggregator='sum')
    inventory_value = fields.Monetary(aggregator='sum')
